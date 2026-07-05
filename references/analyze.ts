import { Router } from 'express';
import { readFileSync } from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { getFeatures } from '../config.js';
import { fetchMarketListings } from './auto-dev.js';
import { errMsg } from '../lib/errors.js';
import { getUserFromRequest } from '../lib/supabaseAdmin.js';
import { isProRequest } from '../lib/entitlements.js';
import { getScanUsage, incrementScanUsage, FREE_SCAN_LIMIT } from '../lib/usage.js';
import type { MarketListings, ScoreFactor } from '../../shared/types.js';

interface Addon { name: string; price: number; }

/**
 * Internal market reference (calculated fair value or live-listings average).
 * Looser than the shared MarketCalc — `source` is a plain string and a couple
 * of MSRP-lookup fields ride along.
 */
interface MarketRef {
  source: string;
  estimated: number | null;
  low: number | null;
  high: number | null;
  baseMsrp: number | null;
  listingCount?: number;
  hasLiveData?: boolean;
  age?: number;
  depFactor?: number;
  allTrims?: unknown;
  matchedTrim?: unknown;
}

/** Deal object (computed values) passed to the scoring engine. */
interface ScoringDeal {
  price: number;
  apr: number;
  term: number;
  creditTier: string;
  down: number;
  tradeIn: number;
  tradeOwed: number;
  docFee: number;
  regFee: number;
  titleFee: number;
  taxAmount: number;
  totalInterest: number;
  state?: string | null;
  zip?: string;
  year?: number | string;
  make?: string;
  model?: string;
  condition?: string;
  addons?: Addon[];
}

interface DocFeeRule {
  capped?: boolean;
  cap?: number;
  percentCap?: number;
  capType?: string;
  law?: string;
  lawSummary?: string;
  typical?: number;
}

/** Per-state fee/tax reference data (state-fees.json entry). */
interface StateData {
  docFee?: DocFeeRule;
  registration?: { estimatedRange?: number[] };
  title?: { fee?: number };
}

/** Result of resolving a state's effective doc-fee cap for a given price. */
interface DocFeeCap {
  hasCap: boolean;
  effectiveCap: number | null;
  flatCap?: number | null;
  percentCap?: number | null;
  percentAmount?: number;
  capType?: string;
  law?: string | null;
  lawSummary?: string | null;
  explanation: string | null;
}

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const router = Router();

const msrpData = JSON.parse(readFileSync(path.join(__dirname, '../data/vehicle-msrp.json'), 'utf-8'));
const stateFees = JSON.parse(readFileSync(path.join(__dirname, '../data/state-fees.json'), 'utf-8'));
const taxRates = JSON.parse(readFileSync(path.join(__dirname, '../data/tax-rates.json'), 'utf-8'));
const taxLaws = JSON.parse(readFileSync(path.join(__dirname, '../data/tax-laws.json'), 'utf-8'));

// Fair APR by credit tier
const FAIR_APR: Record<string, number> = {
  excellent: 5,
  'very-good': 7,
  good: 9,
  fair: 13,
  poor: 18,
};

// Depreciation curve by vehicle age (years)
const DEPRECIATION: Record<number, number> = {
  0: 1.0, 1: 0.82, 2: 0.73, 3: 0.66, 4: 0.60, 5: 0.55,
  6: 0.50, 7: 0.46, 8: 0.42, 9: 0.39,
};

function getDepreciation(age: number) {
  if (age <= 0) return 1.0;
  if (age >= 10) return 0.36;
  return DEPRECIATION[age] || 0.36;
}

function findMsrpKey(make: string, model: string) {
  const search = `${make} ${model}`.toLowerCase();
  return Object.keys(msrpData).find(k => k.toLowerCase() === search)
    || Object.keys(msrpData).find(k =>
      k.toLowerCase().includes(model.toLowerCase()) &&
      k.toLowerCase().includes(make.toLowerCase())
    );
}

function findTrimMsrp(make: string, model: string, year: number, trim?: string) {
  const key = findMsrpKey(make, model);
  if (!key || !msrpData[key]) return null;

  let yearData = msrpData[key][String(year)];

  // Try adjacent years if exact year not found
  if (!yearData) {
    const years = Object.keys(msrpData[key]).map(Number).sort();
    const nearest = years.reduce((prev, curr) =>
      Math.abs(curr - year) < Math.abs(prev - year) ? curr : prev
    );
    yearData = msrpData[key][String(nearest)];
  }

  if (!yearData) return null;

  const trims = Object.keys(yearData);

  if (trim) {
    const t = trim.toLowerCase();
    const matchedTrim = trims.find(k => k.toLowerCase() === t)
      || trims.find(k => k.toLowerCase().includes(t) || t.includes(k.toLowerCase()));
    if (matchedTrim) return { msrp: yearData[matchedTrim], trim: matchedTrim, allTrims: yearData };
  }

  // Return base trim
  return { msrp: yearData[trims[0]], trim: trims[0], allTrims: yearData };
}

/**
 * Calculate the effective legal doc fee cap for a given state + car price.
 * Handles percentage-based rules (e.g., Ohio: lesser of $398 or 10% of price).
 * Returns: { hasCap, effectiveCap, flatCap, percentCap, capType, law, explanation }
 */
function calculateEffectiveDocFeeCap(stateData: StateData | undefined, vehiclePrice: number): DocFeeCap {
  const df = stateData?.docFee;
  if (!df?.capped) {
    return { hasCap: false, effectiveCap: null, law: null, explanation: null };
  }

  const flatCap = df.cap || null;
  const percentCap = df.percentCap || null;
  const law = df.law || null;
  const lawSummary = df.lawSummary || null;

  // Percentage-based rule (e.g., Ohio: lesser of $398 or 10%)
  if (percentCap && flatCap && vehiclePrice > 0) {
    const percentAmount = Math.round(vehiclePrice * percentCap);
    const capType = df.capType || 'lesser-of';

    let effectiveCap;
    let explanation;
    if (capType === 'lesser-of') {
      effectiveCap = Math.min(flatCap, percentAmount);
      const lower = effectiveCap === flatCap ? `$${flatCap} (flat maximum)` : `$${percentAmount} (${(percentCap * 100).toFixed(0)}% of $${vehiclePrice.toLocaleString()})`;
      explanation = `For a $${vehiclePrice.toLocaleString()} vehicle, the effective cap is ${lower}.`;
    } else if (capType === 'greater-of') {
      effectiveCap = Math.max(flatCap, percentAmount);
      explanation = `For a $${vehiclePrice.toLocaleString()} vehicle, the effective cap is $${effectiveCap} (greater of $${flatCap} or ${(percentCap * 100).toFixed(0)}%).`;
    } else {
      effectiveCap = flatCap;
      explanation = `Flat cap of $${flatCap}.`;
    }

    return {
      hasCap: true,
      effectiveCap,
      flatCap,
      percentCap,
      percentAmount,
      capType,
      law,
      lawSummary,
      explanation,
    };
  }

  // Simple flat cap (e.g., CA $85, NY $175)
  return {
    hasCap: true,
    effectiveCap: flatCap,
    flatCap,
    percentCap: null,
    capType: 'flat',
    law,
    lawSummary,
    explanation: `Flat cap of $${flatCap}.`,
  };
}

/**
 * Build a unified market reference for flags/scripts/scoring.
 * Prefers live market listings over calculated fair value when available.
 * Returns: { source: 'listings' | 'calculated', estimated, low, high, baseMsrp, listingCount, hasLiveData }
 */
function buildMarketReference(calculated: MarketRef, listings: MarketListings | null): MarketRef {
  const hasLiveData = listings?.enabled !== false && listings?.avgPrice != null && (listings?.listingCount ?? 0) > 0;

  if (hasLiveData && listings) {
    const avg = listings.avgPrice ?? 0;
    return {
      source: 'listings',
      estimated: listings.avgPrice ?? null,
      low: listings.priceRange?.low || Math.round(avg * 0.9),
      high: listings.priceRange?.high || Math.round(avg * 1.1),
      baseMsrp: calculated?.baseMsrp ?? null,
      listingCount: listings.listingCount,
      hasLiveData: true,
    };
  }

  return {
    source: 'calculated',
    estimated: calculated?.estimated ?? null,
    low: calculated?.low ?? null,
    high: calculated?.high ?? null,
    baseMsrp: calculated?.baseMsrp ?? null,
    listingCount: 0,
    hasLiveData: false,
  };
}

function estimateMarketValue(
  make: string, model: string, year: number, trim?: string,
  condition?: string, mileage?: number | string,
) {
  const msrpInfo = findTrimMsrp(make, model, year, trim);
  let baseMsrp = msrpInfo?.msrp;

  if (!baseMsrp) {
    // No MSRP data — cannot estimate, return null
    return { estimated: null, low: null, high: null, baseMsrp: null, source: 'unknown' };
  }

  if (condition === 'new') {
    return {
      estimated: baseMsrp,
      low: baseMsrp,
      high: Math.round(baseMsrp * 1.15),
      baseMsrp,
      source: 'msrp-data',
      allTrims: msrpInfo?.allTrims,
      matchedTrim: msrpInfo?.trim,
    };
  }

  // Used vehicle
  const currentYear = new Date().getFullYear();
  const age = currentYear - year;
  const depFactor = getDepreciation(age);
  let estimated = Math.round(baseMsrp * depFactor);

  // Mileage adjustment: deduct 1% per 5000 miles over 12000/year average
  if (mileage) {
    const expectedMiles = age * 12000;
    const excessMiles = Number(mileage) - expectedMiles;
    if (excessMiles > 0) {
      const mileagePenalty = Math.floor(excessMiles / 5000) * 0.01;
      estimated = Math.round(estimated * (1 - mileagePenalty));
    }
  }

  return {
    estimated,
    low: Math.round(estimated * 0.92),
    high: Math.round(estimated * 1.08),
    baseMsrp,
    age,
    depFactor,
    source: 'depreciation-model',
    allTrims: msrpInfo?.allTrims,
    matchedTrim: msrpInfo?.trim,
  };
}

function calculatePayment(principal: number, aprPercent: number, termMonths: number) {
  if (principal <= 0 || termMonths <= 0) return 0;
  if (aprPercent <= 0) return Math.round((principal / termMonths) * 100) / 100;

  const r = aprPercent / 100 / 12;
  const n = termMonths;
  const payment = principal * (r * Math.pow(1 + r, n)) / (Math.pow(1 + r, n) - 1);
  return Math.round(payment * 100) / 100;
}

function scoreDeal(deal: ScoringDeal, market: MarketRef, stateData?: StateData, _taxInfo?: unknown) {
  const factors: ScoreFactor[] = [];
  let totalScore = 0;

  // Factor 1: Price vs Market (35 pts max)
  if (market.estimated) {
    const ratio = deal.price / market.estimated;
    let pts;
    if (ratio <= 0.90) pts = 35;
    else if (ratio <= 0.95) pts = 30;
    else if (ratio <= 1.00) pts = 25;
    else if (ratio <= 1.05) pts = 18;
    else if (ratio <= 1.10) pts = 10;
    else if (ratio <= 1.20) pts = 3;
    else if (ratio <= 1.50) pts = 0;
    else pts = -20;

    factors.push({ name: 'Price vs Market', points: pts, max: 35, ratio: Math.round(ratio * 100) / 100 });
    totalScore += pts;
  } else {
    factors.push({ name: 'Price vs Market', points: 15, max: 35, note: 'No market data available — neutral score' });
    totalScore += 15;
  }

  // Factor 2: APR Fairness (20 pts max) — skip for cash deals
  const isCashDeal = !deal.term || deal.term === 0;
  if (!isCashDeal) {
    const fairApr = FAIR_APR[deal.creditTier] || FAIR_APR.good;
    const aprDiff = deal.apr - fairApr;
    let aprPts;
    if (deal.apr > 20) aprPts = -10;
    else if (aprDiff <= 0) aprPts = 20;
    else if (aprDiff <= 2) aprPts = 15;
    else if (aprDiff <= 5) aprPts = 8;
    else aprPts = 0;

    factors.push({ name: 'APR Fairness', points: aprPts, max: 20, apr: deal.apr, fairApr });
    totalScore += aprPts;
  } else {
    factors.push({ name: 'APR Fairness', points: 20, max: 20, note: 'Cash deal — no financing' });
    totalScore += 20;
  }

  // Factor 3: Fees vs Norms (15 pts max)
  const docFee = deal.docFee || 0;
  let feePts = 15;
  const docFeeCapInfo = calculateEffectiveDocFeeCap(stateData, deal.price);
  if (docFeeCapInfo.hasCap) {
    const cap = docFeeCapInfo.effectiveCap ?? 0;
    if (docFee <= cap) feePts = 15;
    else if (docFee <= cap * 1.1) feePts = 10;
    else feePts = 5;
  } else {
    if (docFee <= 75) feePts = 15;
    else if (docFee <= 300) feePts = 12;
    else if (docFee <= 500) feePts = 8;
    else feePts = 0;
  }

  factors.push({ name: 'Fees', points: feePts, max: 15, docFee });
  totalScore += feePts;

  // Factor 4: Add-ons (15 pts max)
  const totalAddons = (deal.addons || []).reduce((sum, a) => sum + (a.price || 0), 0);
  let addonPts;
  if (totalAddons === 0) addonPts = 15;
  else if (totalAddons <= 500) addonPts = 12;
  else if (totalAddons <= 1500) addonPts = 8;
  else if (totalAddons <= 3000) addonPts = 4;
  else if (totalAddons <= 5000) addonPts = 0;
  else addonPts = -5;

  factors.push({ name: 'Add-ons', points: addonPts, max: 15, totalAddons });
  totalScore += addonPts;

  // Factor 5: Loan Term (8 pts max) — skip for cash deals
  if (!isCashDeal) {
    let termPts;
    if (deal.term <= 36) termPts = 8;
    else if (deal.term <= 48) termPts = 7;
    else if (deal.term <= 60) termPts = 5;
    else if (deal.term <= 72) termPts = 2;
    else termPts = 0;

    factors.push({ name: 'Loan Term', points: termPts, max: 8, term: deal.term });
    totalScore += termPts;
  } else {
    factors.push({ name: 'Loan Term', points: 8, max: 8, note: 'Cash deal — no loan' });
    totalScore += 8;
  }

  // Factor 6: Down Payment / Equity (7 pts max)
  const equity = (deal.down || 0) + Math.max(0, (deal.tradeIn || 0) - (deal.tradeOwed || 0));
  const totalCost = deal.price + (deal.taxAmount || 0) + (deal.docFee || 0) + (deal.regFee || 0) + (deal.titleFee || 0) + totalAddons;
  const downRatio = totalCost > 0 ? equity / totalCost : 0;
  const hasNegativeEquity = (deal.tradeOwed || 0) > (deal.tradeIn || 0);

  let downPts;
  if (hasNegativeEquity) downPts = -5;
  else if (downRatio >= 0.20) downPts = 7;
  else if (downRatio >= 0.10) downPts = 5;
  else if (downRatio >= 0.05) downPts = 3;
  else downPts = 0;

  factors.push({ name: 'Down Payment', points: downPts, max: 7, downRatio: Math.round(downRatio * 100) / 100 });
  totalScore += downPts;

  // Extreme overpay penalty: when price is way above market, cap the score
  // This ensures a $80k Prius (ratio ~2.4) can't score above 15 just because
  // the APR, fees, and addons are normal
  if (market.estimated) {
    const ratio = deal.price / market.estimated;
    if (ratio > 2.0) {
      totalScore = Math.min(totalScore, 5);
    } else if (ratio > 1.5) {
      totalScore = Math.min(totalScore, 15);
    } else if (ratio > 1.30) {
      totalScore = Math.min(totalScore, 30);
    }
  }

  // Clamp score
  const finalScore = Math.max(0, Math.min(100, totalScore));

  let label;
  if (finalScore >= 85) label = 'Excellent Deal';
  else if (finalScore >= 70) label = 'Good Deal';
  else if (finalScore >= 50) label = 'Fair Deal';
  else if (finalScore >= 30) label = 'Below Average';
  else label = 'Poor Deal — Walk Away';

  return { score: finalScore, label, factors };
}

function generateFlags(deal: ScoringDeal, market: MarketRef, stateData?: StateData) {
  const redFlags = [];
  const greenFlags = [];
  const totalAddons = (deal.addons || []).reduce((sum, a) => sum + (a.price || 0), 0);

  // Red flags — use marketRef (live listings when available, calculated otherwise)
  const refSourceLabel = market.hasLiveData
    ? `market average of ${market.listingCount} active listings`
    : 'calculated fair market price';
  const refNoticeCalc = market.hasLiveData
    ? ''
    : ' (no active listings found in market — based on depreciation model)';

  if (market.estimated) {
    const ratio = deal.price / market.estimated;
    if (ratio > 1.30) {
      redFlags.push({
        severity: 'critical',
        title: 'Significantly Above Market',
        detail: `Vehicle priced $${Math.round(deal.price - market.estimated).toLocaleString()} above the ${refSourceLabel}. Price ratio: ${Math.round(ratio * 100)}% of market.${refNoticeCalc}`,
        action: 'Negotiate down or walk away.',
      });
    } else if (ratio > 1.15) {
      redFlags.push({
        severity: 'warning',
        title: 'Above Market Value',
        detail: `Vehicle priced $${Math.round(deal.price - market.estimated).toLocaleString()} above the ${refSourceLabel}.${refNoticeCalc}`,
        action: 'Negotiate the price closer to market value.',
      });
    }

    if (deal.condition === 'new' && market.baseMsrp && deal.price > market.baseMsrp * 1.20) {
      redFlags.push({
        severity: 'critical',
        title: 'Possible Dealer Markup / ADM',
        detail: `Price is ${Math.round((deal.price / market.baseMsrp - 1) * 100)}% above MSRP of $${market.baseMsrp.toLocaleString()}.`,
        action: 'Ask dealer to remove any Additional Dealer Markup.',
      });
    }
  }

  const fairApr = FAIR_APR[deal.creditTier] || FAIR_APR.good;
  if (deal.apr > fairApr + 3) {
    redFlags.push({
      severity: 'warning',
      title: 'High APR',
      detail: `APR of ${deal.apr}% is ${(deal.apr - fairApr).toFixed(1)}% above typical for ${deal.creditTier} credit.`,
      action: 'Get a pre-approval from a credit union before accepting this rate.',
    });
  }

  const docCapInfo = calculateEffectiveDocFeeCap(stateData, deal.price);
  if (docCapInfo.hasCap && deal.docFee > (docCapInfo.effectiveCap ?? 0)) {
    redFlags.push({
      severity: 'critical',
      title: 'Doc Fee Exceeds Legal Cap',
      detail: `Doc fee of $${deal.docFee} exceeds the legal maximum of $${docCapInfo.effectiveCap} per ${docCapInfo.law}. ${docCapInfo.explanation}`,
      action: 'Tell the dealer to reduce the doc fee to the legal limit.',
    });
  } else if (!docCapInfo.hasCap) {
    const typicalDocFee = stateData?.docFee?.typical || 150;
    if (deal.docFee > typicalDocFee * 1.5) {
      redFlags.push({
        severity: 'warning',
        title: 'High Documentation Fee',
        detail: `Doc fee of $${deal.docFee} is well above the typical $${typicalDocFee} for your state.`,
        action: 'Negotiate the doc fee down — the state average is around $' + typicalDocFee + '.',
      });
    } else if (deal.docFee >= 500) {
      redFlags.push({
        severity: 'warning',
        title: 'High Documentation Fee',
        detail: `Doc fee of $${deal.docFee} is significantly above the national average of $75-150.`,
        action: 'Negotiate the doc fee down.',
      });
    }
  }

  // Registration fee check against state data
  if (deal.regFee && stateData?.registration?.estimatedRange) {
    const [low, high] = stateData.registration.estimatedRange;
    if (deal.regFee > high * 2) {
      redFlags.push({
        severity: 'warning',
        title: 'Registration Fee Seems Too High',
        detail: `Registration fee of $${deal.regFee} is well above the typical $${low}–$${high} range for ${deal.state || 'your state'}.`,
        action: 'Verify this amount with your DMV. Dealers sometimes inflate registration estimates.',
      });
    }
  } else if (deal.regFee > 500) {
    redFlags.push({
      severity: 'warning',
      title: 'High Registration Fee',
      detail: `Registration fee of $${deal.regFee} is unusually high. Most states charge $50–$300.`,
      action: 'Ask the dealer to itemize this fee and verify with your local DMV.',
    });
  }

  // Title fee check
  if (deal.titleFee && stateData?.title?.fee) {
    if (deal.titleFee > stateData.title.fee * 2) {
      redFlags.push({
        severity: 'warning',
        title: 'Title Fee Seems Too High',
        detail: `Title fee of $${deal.titleFee} is above the typical $${stateData.title.fee} for ${deal.state || 'your state'}.`,
        action: 'State title fees are fixed — verify this amount is correct.',
      });
    }
  }

  (deal.addons || []).forEach(addon => {
    if (addon.price > 2000) {
      redFlags.push({
        severity: 'warning',
        title: `Expensive Add-on: ${addon.name}`,
        detail: `${addon.name} at $${addon.price.toLocaleString()} is unusually expensive.`,
        action: 'Consider removing or getting third-party coverage.',
      });
    }
  });

  if (totalAddons > 3000) {
    redFlags.push({
      severity: 'warning',
      title: 'High Total Add-ons',
      detail: `Total add-ons of $${totalAddons.toLocaleString()} — review each for necessity.`,
      action: 'Remove any add-ons you didn\'t specifically request.',
    });
  }

  if (deal.term > 72) {
    redFlags.push({
      severity: 'warning',
      title: 'Long Loan Term',
      detail: `Loan term of ${deal.term} months means significantly more interest paid.`,
      action: 'Try to keep the term at 60 months or less.',
    });
  }

  if ((deal.tradeOwed || 0) > (deal.tradeIn || 0)) {
    const negEquity = deal.tradeOwed - deal.tradeIn;
    redFlags.push({
      severity: 'critical',
      title: 'Negative Equity',
      detail: `You owe $${negEquity.toLocaleString()} more than your trade-in value. This gets added to your new loan.`,
      action: 'Consider paying off more of the trade-in before purchasing.',
    });
  }

  // Interest burden
  if (deal.totalInterest && deal.price) {
    const interestRatio = deal.totalInterest / deal.price;
    if (interestRatio > 0.30) {
      redFlags.push({
        severity: 'warning',
        title: 'High Interest Burden',
        detail: `You'll pay $${Math.round(deal.totalInterest).toLocaleString()} in interest — ${Math.round(interestRatio * 100)}% of vehicle price.`,
        action: 'Consider a shorter term or lower APR.',
      });
    }
  }

  // Green flags
  if (market.estimated && deal.price <= market.estimated * 0.95) {
    greenFlags.push({
      title: 'Below Market Price',
      detail: `Price is ${Math.round((1 - deal.price / market.estimated) * 100)}% below the ${market.hasLiveData ? `market average of ${market.listingCount} active listings` : 'calculated fair market price'}.`,
    });
  }

  if (deal.apr <= 4) {
    greenFlags.push({
      title: 'Excellent Interest Rate',
      detail: `APR of ${deal.apr}% is an excellent rate.`,
    });
  }

  if (deal.docFee <= 100) {
    greenFlags.push({
      title: 'Reasonable Doc Fee',
      detail: `Documentation fee of $${deal.docFee} is very reasonable.`,
    });
  }

  if (totalAddons === 0 || totalAddons < 500) {
    greenFlags.push({
      title: 'Minimal Add-ons',
      detail: totalAddons === 0 ? 'No add-ons — clean deal.' : `Only $${totalAddons} in add-ons.`,
    });
  }

  const equity = (deal.down || 0) + Math.max(0, (deal.tradeIn || 0) - (deal.tradeOwed || 0));
  const totalCost = deal.price + (deal.taxAmount || 0) + (deal.docFee || 0) + (deal.regFee || 0) + totalAddons;
  if (totalCost > 0 && equity / totalCost >= 0.20) {
    greenFlags.push({
      title: 'Strong Down Payment',
      detail: `${Math.round((equity / totalCost) * 100)}% down reduces loan risk and monthly payment.`,
    });
  }

  if (deal.term <= 48) {
    greenFlags.push({
      title: 'Short Loan Term',
      detail: `${deal.term}-month term saves on total interest paid.`,
    });
  }

  if (deal.totalInterest && deal.price && deal.totalInterest / deal.price < 0.10) {
    greenFlags.push({
      title: 'Low Interest Burden',
      detail: `Total interest is only ${Math.round((deal.totalInterest / deal.price) * 100)}% of vehicle price.`,
    });
  }

  return { redFlags, greenFlags };
}

function generateNegotiationScripts(deal: ScoringDeal, market: MarketRef, stateData?: StateData) {
  const scripts = [];

  if (market.estimated && deal.price > market.estimated * 1.05) {
    const diff = Math.round(deal.price - market.estimated);
    const target = Math.round(market.estimated * 1.02);
    let script;
    if (market.hasLiveData) {
      script = `"I've researched the ${deal.year} ${deal.make} ${deal.model} and comparable vehicles are currently listed for around $${market.estimated.toLocaleString()} (based on ${market.listingCount} active listings). Your asking price of $${deal.price.toLocaleString()} is $${diff.toLocaleString()} above the market average. Can we work toward $${target.toLocaleString()}?"`;
    } else {
      script = `"Based on the calculated fair market price for a ${deal.year} ${deal.make} ${deal.model}, comparable vehicles should be around $${market.estimated.toLocaleString()}. Your asking price of $${deal.price.toLocaleString()} is $${diff.toLocaleString()} above fair value. Can we work toward $${target.toLocaleString()}? (Note: we couldn't find active listings for this exact vehicle — this is based on our depreciation model.)"`;
    }
    scripts.push({
      issue: 'Price Above Market',
      script,
      source: market.hasLiveData ? 'live-listings' : 'calculated',
    });
  }

  const fairApr = FAIR_APR[deal.creditTier] || FAIR_APR.good;
  if (deal.apr > fairApr + 2) {
    const betterRate = (fairApr + 1).toFixed(1);
    scripts.push({
      issue: 'High APR',
      script: `"I have a pre-approval from my credit union at ${betterRate}%. Can you match or beat that rate?"`,
    });
  }

  // Doc fee negotiation — state-aware with legal cap calculation
  const scriptDocCap = calculateEffectiveDocFeeCap(stateData, deal.price);
  if (scriptDocCap.hasCap) {
    const lawRef = scriptDocCap.law ? ` per ${scriptDocCap.law}` : '';
    const stateLabel = deal.state ? `in ${deal.state}${deal.zip ? ` (based on ZIP: ${deal.zip})` : ''}` : '';

    if (deal.docFee > (scriptDocCap.effectiveCap ?? 0)) {
      // Exceeds legal cap
      let capText;
      if (scriptDocCap.percentCap && scriptDocCap.capType === 'lesser-of') {
        capText = `capped at $${scriptDocCap.flatCap} or ${(scriptDocCap.percentCap * 100).toFixed(0)}% of the vehicle's cash price, whichever is less`;
      } else if (scriptDocCap.percentCap && scriptDocCap.capType === 'greater-of') {
        capText = `the greater of $${scriptDocCap.flatCap} or ${(scriptDocCap.percentCap * 100).toFixed(0)}% of the vehicle's cash price`;
      } else {
        capText = `capped at $${scriptDocCap.flatCap}`;
      }
      scripts.push({
        issue: 'Doc Fee Over Legal Cap',
        script: `"The doc fee ${stateLabel} is ${capText}${lawRef}. For this $${deal.price.toLocaleString()} vehicle, the maximum allowed is $${scriptDocCap.effectiveCap}. You've charged $${deal.docFee} — please correct this to the legal limit of $${scriptDocCap.effectiveCap}."`,
      });
    } else if (scriptDocCap.percentCap && scriptDocCap.capType === 'lesser-of' && deal.docFee === scriptDocCap.flatCap && (scriptDocCap.percentAmount ?? 0) < (scriptDocCap.flatCap ?? 0)) {
      // Fee is AT the flat cap but percent-based cap would be lower
      // e.g., OH: dealer charged $398 but 10% of $15,796 = $1,580 → NOT applicable (flat is lower)
      // This branch is skipped — keep for future reverse scenarios
    } else if (deal.docFee > (stateData?.docFee?.typical || 150)) {
      // Within legal cap but above typical — suggest negotiating to lower amount
      const typical = stateData?.docFee?.typical || 150;
      const target = Math.min(deal.docFee, Math.max(typical, 150));
      scripts.push({
        issue: 'High Doc Fee',
        script: `"The doc fee ${stateLabel} is legally capped at $${scriptDocCap.effectiveCap}${lawRef}, but that's a maximum — not a required amount. The typical doc fee is around $${typical}. I'd like to negotiate this down to $${target}."`,
      });
    }
  } else if (deal.docFee > 300) {
    // No state cap — use national average as reference
    scripts.push({
      issue: 'High Doc Fee',
      script: `"The doc fee of $${deal.docFee} is well above the national average of $75-150. I'd like to negotiate this down to $${Math.min(deal.docFee, 150)}."`,
    });
  }

  (deal.addons || []).forEach(addon => {
    if (addon.price > 500) {
      scripts.push({
        issue: `Remove ${addon.name}`,
        script: `"I'd like to remove ${addon.name} ($${addon.price.toLocaleString()}). I can get equivalent coverage from a third-party provider for significantly less."`,
      });
    }
  });

  return scripts;
}

// POST /api/analyze
router.post('/', async (req, res) => {
  try {
    const {
      year, make, model, trim, condition, mileage, zip,
      price, down, tradeIn, tradeOwed, apr, term, creditTier,
      docFee, regFee, titleFee, addons,
    } = req.body ?? {};

    // Validate required fields (financing is optional — cash deals have no term/apr)
    if (!price || !zip || !year || !make || !model || !condition) {
      return res.status(400).json({ error: 'Missing required fields: price, zip, year, make, model, condition' });
    }

    // Free-tier gate: a deal analysis requires a signed-in account, and non-Pro
    // users get FREE_SCAN_LIMIT free analyses before they must upgrade. Pro is
    // unlimited and never metered. (OCR stays open so users can still see the
    // extraction work before signing up — only the scored result is gated.)
    const user = await getUserFromRequest(req);
    if (!user) {
      return res.status(401).json({ error: 'sign_in_required' });
    }
    const isPro = await isProRequest(req);
    if (!isPro) {
      const used = await getScanUsage(user.id);
      if (used >= FREE_SCAN_LIMIT) {
        return res.status(403).json({ error: 'free_limit_reached', used, limit: FREE_SCAN_LIMIT });
      }
    }

    // Auto-correct condition: a car 2+ years old cannot be "new"
    const currentYear = new Date().getFullYear();
    const vehicleAge = currentYear - parseInt(year);
    const effectiveCondition = (vehicleAge >= 2 && condition === 'new') ? 'used' : condition;

    // Get state from ZIP
    const { zipToState } = await import('./tax.js');
    const zipPrefix = Math.floor(parseInt(zip, 10) / 100);
    const state = zipToState(zipPrefix);

    if (!state) {
      return res.status(400).json({ error: 'Could not determine state from ZIP' });
    }

    // Get tax data
    const stateData = stateFees[state] || {};
    const taxData = taxRates[state] || {};

    // Check if NYC
    const z = parseInt(zip, 10);
    const isNYC = state === 'NY' && ((z >= 10000 && z <= 10499) || (z >= 11000 && z <= 11999));

    let taxRate = taxData.stateRate + taxData.avgLocalRate;
    if (isNYC) taxRate = 0.08875;

    // Calculate tax
    // In most states, trade-in reduces taxable amount
    const taxableAmount = Math.max(0, price - (tradeIn || 0));
    const taxAmount = Math.round(taxableAmount * taxRate * 100) / 100;

    // Total fees — use nullish coalescing (??) so user-entered $0 is respected
    const totalDocFee = (docFee != null && docFee !== '') ? parseFloat(docFee) : (stateData?.docFee?.typical || stateData?.docFee?.cap || 0);
    const totalRegFee = (regFee != null && regFee !== '') ? parseFloat(regFee) : (stateData?.registration?.estimatedRange?.[0] || 0);
    const totalTitleFee = (titleFee != null && titleFee !== '') ? parseFloat(titleFee) : (stateData?.title?.fee || 0);
    const totalAddons = (addons || []).reduce((sum: number, a: { price?: number }) => sum + (a.price || 0), 0);

    // Total cost
    const totalCost = price + taxAmount + totalDocFee + totalRegFee + totalTitleFee + totalAddons;

    // Loan amount
    const equity = (down || 0) + Math.max(0, (tradeIn || 0) - (tradeOwed || 0));
    const negativeEquity = Math.max(0, (tradeOwed || 0) - (tradeIn || 0));
    const loanAmount = Math.max(0, totalCost - equity + negativeEquity);

    // Payment (skip for cash deals)
    const effectiveTerm = parseInt(term) || 0;
    const monthlyPayment = effectiveTerm > 0 ? calculatePayment(loanAmount, apr || 6, effectiveTerm) : 0;
    const totalPaid = effectiveTerm > 0 ? Math.round(monthlyPayment * effectiveTerm * 100) / 100 : Math.round(totalCost * 100) / 100;
    const totalInterest = effectiveTerm > 0 ? Math.round((totalPaid - loanAmount) * 100) / 100 : 0;

    // Market estimation (calculated fair value from depreciation model)
    const market = estimateMarketValue(make, model, parseInt(year), trim, effectiveCondition, mileage);

    // Fetch live market listings from Auto.dev (for flags/scripts/dual-bar display)
    let listings = null;
    try {
      listings = await fetchMarketListings(year, make, model, mileage, zip);
    } catch (err) {
      console.warn('Listings fetch failed, using calculated value only:', errMsg(err));
    }

    // Build unified market reference — prefers live listings, falls back to calculated
    const marketRef = buildMarketReference(market, listings);

    // Build the deal object with computed values for scoring
    const dealForScoring = {
      ...req.body,
      price: parseFloat(price),
      down: parseFloat(down) || 0,
      tradeIn: parseFloat(tradeIn) || 0,
      tradeOwed: parseFloat(tradeOwed) || 0,
      apr: parseFloat(apr) || 0,
      term: parseInt(term) || 0,
      docFee: totalDocFee,
      regFee: totalRegFee,
      titleFee: totalTitleFee,
      state,
      taxAmount,
      totalInterest,
      creditTier: creditTier || 'good',
      condition: effectiveCondition,
      addons: addons || [],
    };

    // Score the deal — use marketRef (live listings when available, calculated otherwise)
    // so the score is consistent with flags and scripts
    const scoring = scoreDeal(dealForScoring, marketRef, stateData);

    // Generate flags (uses marketRef — prefers live listings over calculated)
    const flags = generateFlags(dealForScoring, marketRef, stateData);

    // Negotiation scripts (uses marketRef — prefers live listings over calculated)
    const scripts = generateNegotiationScripts(dealForScoring, marketRef, stateData);

    // Tax law
    const taxLaw = taxLaws[state] || null;

    // Count this successful analysis against the free tier (Pro is unlimited).
    if (!isPro) await incrementScanUsage(user.id);

    res.json({
      score: scoring.score,
      label: scoring.label,
      factors: scoring.factors,
      vehicle: { year, make, model, trim, condition: effectiveCondition, conditionCorrected: effectiveCondition !== condition, mileage },
      entered: {
        price: parseFloat(price),
        down: parseFloat(down) || 0,
        tradeIn: parseFloat(tradeIn) || 0,
        tradeOwed: parseFloat(tradeOwed) || 0,
        apr: parseFloat(apr) || 6,
        term: parseInt(term),
        creditTier: creditTier || 'good',
        addons: addons || [],
      },
      calculated: {
        state,
        taxRate: Math.round(taxRate * 10000) / 100,
        taxAmount,
        taxLaw,
        docFee: totalDocFee,
        docFeeLaw: stateData?.docFee?.law || null,
        docFeeCap: stateData?.docFee?.capped ? stateData.docFee.cap : null,
        regFee: totalRegFee,
        titleFee: totalTitleFee,
        totalAddons,
        totalCost: Math.round(totalCost * 100) / 100,
        loanAmount: Math.round(loanAmount * 100) / 100,
        monthlyPayment,
        totalInterest,
        totalPaid: Math.round(totalPaid * 100) / 100,
      },
      market: {
        calculated: market,
        listings, // populated server-side during analyze (from Auto.dev)
        reference: marketRef, // unified ref used by flags/scripts
      },
      features: getFeatures(),
      flags,
      scripts,
    });
  } catch (err) {
    console.error('Analysis error:', err);
    res.status(500).json({ error: 'Analysis failed', message: errMsg(err) });
  }
});

export default router;

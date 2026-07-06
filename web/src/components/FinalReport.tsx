import { useEffect, useState } from 'react';

interface FinalReportProps {
  report: any;
}

type Verdict = 'Walk Away' | 'Reconsider' | 'Proceed with Caution' | 'Financially Sound';

// Darker shades of the token hues so large text keeps >=3:1 on the light bg.
const VERDICT_STYLES: Record<Verdict, { color: string; tint: string; sign: string }> = {
  'Walk Away': {
    color: '#c22839',
    tint: 'rgba(255, 71, 87, 0.08)',
    sign: 'Walk away from this deal unless the dealer fixes every deal-breaker below.',
  },
  'Reconsider': {
    color: '#c22839',
    tint: 'rgba(255, 71, 87, 0.06)',
    sign: 'Do not sign as-is. Reconsider unless the items below are corrected in writing.',
  },
  'Proceed with Caution': {
    color: '#8a6d00',
    tint: 'rgba(245, 197, 24, 0.10)',
    sign: 'Proceed only after the flagged items below are resolved.',
  },
  'Financially Sound': {
    color: '#1a7f4b',
    tint: 'rgba(46, 213, 115, 0.08)',
    sign: 'This deal is fair. Still confirm the flagged items before signing.',
  },
};

// The advisor ends with "Financial Wisdom Rating: <one of four>". Prose is
// LLM text, so fall back to the deterministic score if the phrase is missing.
function extractVerdict(advice: string, score: number): Verdict {
  const match = (advice || '').match(
    /Financial Wisdom Rating:?\**\s*(Financially Sound|Proceed with Caution|Reconsider|Walk Away)/i
  );
  if (match) {
    const found = (Object.keys(VERDICT_STYLES) as Verdict[]).find(
      v => v.toLowerCase() === match[1].toLowerCase()
    );
    if (found) return found;
  }
  if (score < 30) return 'Walk Away';
  if (score < 50) return 'Reconsider';
  if (score < 75) return 'Proceed with Caution';
  return 'Financially Sound';
}

// First sentences of "THE BOTTOM LINE" section, if the advisor produced one.
function extractBottomLine(advice: string): string | null {
  const match = (advice || '').match(/THE BOTTOM LINE:?\**\s*\n+([\s\S]{20,600}?)(?:\n\s*\n|\*\*Financial)/i);
  if (!match) return null;
  const sentences = match[1].replace(/\*\*/g, '').trim().split(/(?<=[.!?])\s+/);
  return sentences.slice(0, 2).join(' ');
}

// Light cleanup so raw markdown tokens don't show in the collapsed prose.
function cleanProse(text: string): string {
  return (text || '')
    .replace(/^#{1,6}\s*/gm, '')
    .replace(/\*\*/g, '')
    .replace(/^\s*\*\s+/gm, '• ');
}

const ScoreDial = ({ score }: { score: number }) => {
  const [animatedScore, setAnimatedScore] = useState(0);

  useEffect(() => {
    let current = 0;
    const interval = setInterval(() => {
      if (current >= score) {
        setAnimatedScore(score);
        clearInterval(interval);
      } else {
        current += 1;
        setAnimatedScore(current);
      }
    }, 15);
    return () => clearInterval(interval);
  }, [score]);

  // SVG calculations for a half-circle gauge
  const radius = 80;
  const strokeWidth = 16;
  const circumference = Math.PI * radius;
  const strokeDashoffset = circumference - (animatedScore / 100) * circumference;
  
  let color = 'var(--success-color)'; // Green
  if (animatedScore < 50) color = 'var(--error-color)'; // Red
  else if (animatedScore < 75) color = 'var(--accent-color)'; // Yellow

  return (
    <div style={{ position: 'relative', width: '200px', height: '110px', margin: '0 auto', textAlign: 'center' }}>
      <svg width="200" height="110" style={{ transform: 'rotate(180deg)' }}>
        <circle
          cx="100" cy="10" r={radius}
          fill="none"
          stroke="var(--bg-color)"
          strokeWidth={strokeWidth}
          strokeDasharray={circumference}
          strokeDashoffset="0"
          style={{ filter: 'drop-shadow(3px 3px 6px var(--shadow-dark)) drop-shadow(-3px -3px 6px var(--shadow-light))' }}
        />
        <circle
          cx="100" cy="10" r={radius}
          fill="none"
          stroke={color}
          strokeWidth={strokeWidth}
          strokeDasharray={circumference}
          strokeDashoffset={strokeDashoffset}
          style={{ transition: 'stroke-dashoffset 0.1s linear', strokeLinecap: 'round' }}
        />
      </svg>
      <div style={{ position: 'absolute', bottom: '0', left: '0', width: '100%' }}>
        <span style={{ fontSize: '2.5rem', fontWeight: 800, color: 'var(--text-ink)' }}>{animatedScore}</span>
      </div>
    </div>
  );
};

export function FinalReport({ report }: FinalReportProps) {
  if (!report) return null;

  const { arena_result, score_result, score_narrative, financial_advice } = report;
  const cf = arena_result?.counterfactual;
  const debates = arena_result?.debates || [];
  const factors = score_result?.factors || [];

  // Try to find the score from either place
  const finalScore = cf?.original_score ?? score_result?.score ?? 0;

  const verdict = extractVerdict(financial_advice, finalScore);
  const verdictStyle = VERDICT_STYLES[verdict];
  const bottomLine = extractBottomLine(financial_advice);

  // Deterministic flags from the scoring engine — critical first, capped.
  const redFlags: any[] = [...(score_result?.red_flags || [])].sort(
    (a, b) => (a.severity === 'critical' ? 0 : 1) - (b.severity === 'critical' ? 0 : 1)
  );
  const shownFlags = redFlags.slice(0, 5);
  const hiddenFlagCount = redFlags.length - shownFlags.length;
  const greenFlags: any[] = score_result?.green_flags || [];

  const handleCopy = (text: string) => {
    navigator.clipboard.writeText(text);
    // Simple visual feedback could be added here
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '2rem' }}>

      {/* VERDICT BANNER — the one-glance answer */}
      <div className="neo-card" style={{ padding: '2rem', backgroundColor: verdictStyle.tint, border: `1px solid ${verdictStyle.color}` }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: '1rem', flexWrap: 'wrap' }}>
          <span style={{ fontSize: '2.2rem', fontWeight: 800, color: verdictStyle.color, letterSpacing: '-0.02em' }}>
            {verdict}
          </span>
          <span style={{ fontSize: '1rem', fontWeight: 600, color: 'var(--text-ink)' }}>
            Deal score {finalScore}/100{score_result?.label ? ` · ${score_result.label}` : ''}
          </span>
        </div>
        <p style={{ marginTop: '0.75rem', fontSize: '1.1rem', fontWeight: 600, color: 'var(--text-ink)' }}>
          {verdictStyle.sign}
        </p>
        {bottomLine && (
          <p style={{ marginTop: '0.75rem', fontSize: '0.95rem', color: 'var(--text-ink)', maxWidth: '70ch' }}>
            {bottomLine}
          </p>
        )}
      </div>

      {/* WHAT MATTERS MOST — deterministic flags, critical first */}
      {shownFlags.length > 0 && (
        <div className="neo-card">
          <h3 style={{ marginBottom: '1.25rem' }}>What matters most</h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.9rem' }}>
            {shownFlags.map((flag: any, idx: number) => {
              const isCritical = flag.severity === 'critical';
              const chipColor = isCritical ? '#c22839' : '#8a6d00';
              return (
                <div key={idx} style={{ display: 'flex', gap: '0.75rem', alignItems: 'flex-start' }}>
                  <span style={{
                    flexShrink: 0,
                    marginTop: '2px',
                    padding: '2px 10px',
                    borderRadius: '10px',
                    fontSize: '0.7rem',
                    fontWeight: 700,
                    textTransform: 'uppercase',
                    color: chipColor,
                    border: `1px solid ${chipColor}`,
                  }}>
                    {isCritical ? 'Critical' : 'Warning'}
                  </span>
                  <p style={{ fontSize: '0.95rem', maxWidth: '75ch' }}>
                    <strong>{flag.title}.</strong>{' '}
                    {(flag.detail || '').length > 160 ? `${flag.detail.slice(0, 160)}…` : flag.detail}
                  </p>
                </div>
              );
            })}
          </div>
          {hiddenFlagCount > 0 && (
            <p style={{ marginTop: '0.9rem', fontSize: '0.85rem', color: '#5c6478' }}>
              {hiddenFlagCount} more warning{hiddenFlagCount > 1 ? 's' : ''} in the full narrative below.
            </p>
          )}
          {greenFlags.length > 0 && (
            <p style={{ marginTop: '0.9rem', fontSize: '0.9rem', color: '#1a7f4b', fontWeight: 600 }}>
              Going for you: {greenFlags.map((g: any) => g.title).join(' · ')}
            </p>
          )}
        </div>
      )}

      {/* ARENA COUNTERFACTUAL BANNER (LOUDEST ELEMENT) */}
      {cf && (
        <div className="neo-card" style={{ 
          borderLeft: '8px solid var(--accent-color)', 
          background: 'linear-gradient(135deg, var(--bg-color), #fdfdfd)',
          padding: '2rem',
          textAlign: 'center'
        }}>
          <h2 style={{ fontSize: '2rem', marginBottom: '0.5rem', color: 'var(--accent-hover)' }}>
            Win negotiable items
          </h2>
          <p style={{ fontSize: '1.25rem', fontWeight: 600 }}>
            Score: {cf.original_score} → <span style={{ color: 'var(--success-color)', fontSize: '1.5rem' }}>{cf.new_score}</span>
          </p>
          <p style={{ fontSize: '1.5rem', fontWeight: 800, marginTop: '1rem' }}>
            Save <span style={{ color: 'var(--success-color)' }}>${cf.estimated_savings?.toLocaleString() ?? 0}</span>
          </p>
        </div>
      )}

      <div className="neo-card" style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
        <h3 style={{ textAlign: 'center', marginBottom: '1rem' }}>Overall Deal Score</h3>
        <ScoreDial score={finalScore} />
        
        {/* Factor Breakdown Bars */}
        {factors.length > 0 && (
          <div style={{ width: '100%', marginTop: '3rem' }}>
            <h4 style={{ marginBottom: '1.5rem', fontSize: '1rem', color: '#8a96a8', textTransform: 'uppercase', textAlign: 'center' }}>Score Factors</h4>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: '1.5rem' }}>
              {factors.map((factor: any, idx: number) => {
                const value = factor.max > 0 ? (factor.points / factor.max) * 100 : 0;
                return (
                  <div key={idx}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.5rem', fontSize: '0.85rem', fontWeight: 600 }}>
                      <span>{factor.name.replace(/_/g, ' ').toUpperCase()}</span>
                      <span>{factor.points}/{factor.max}</span>
                    </div>
                    <div style={{ width: '100%', height: '12px', backgroundColor: 'var(--bg-color)', borderRadius: '6px', boxShadow: 'var(--neo-inset)' }}>
                      <div style={{ 
                        width: `${Math.max(0, Math.min(100, value))}%`, 
                        height: '100%', 
                        backgroundColor: value > 75 ? 'var(--success-color)' : (value > 50 ? 'var(--accent-color)' : 'var(--error-color)'), 
                        borderRadius: '6px' 
                      }} />
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>

      {/* FLAGS & SCRIPTS */}
      {debates.length > 0 && (
        <div className="neo-card">
          <h3 style={{ marginBottom: '1.5rem' }}>Negotiation Playbook</h3>
          {debates.map((d: any, idx: number) => {
            const isWin = d.conceded === true;
            return (
              <div key={idx} className="neo-card-recessed" style={{ 
                marginBottom: '1.5rem', 
                borderLeft: `6px solid ${isWin ? 'var(--success-color)' : 'var(--error-color)'}` 
              }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '1rem' }}>
                  <div style={{ flex: 1, minWidth: '200px' }}>
                    <h4 style={{ fontSize: '1.2rem', marginBottom: '0.5rem', color: 'var(--text-ink)' }}>{d.issue ? d.issue.replace(/_/g, ' ').toUpperCase() : 'ISSUE'}</h4>
                    <p style={{ fontSize: '0.9rem', color: '#666', marginBottom: '1rem', fontStyle: 'italic' }}>
                      <strong>Citation:</strong> {d.citation || 'N/A'}
                    </p>
                  </div>
                  <span style={{ 
                    padding: '6px 12px', 
                    borderRadius: '20px', 
                    fontSize: '0.75rem', 
                    fontWeight: 'bold',
                    textTransform: 'uppercase',
                    backgroundColor: isWin ? 'rgba(46, 213, 115, 0.1)' : 'rgba(255, 71, 87, 0.1)',
                    color: isWin ? 'var(--success-color)' : 'var(--error-color)',
                    border: `1px solid ${isWin ? 'var(--success-color)' : 'var(--error-color)'}`
                  }}>
                    {isWin ? 'Dealer Conceded' : 'Dealer Pushback'}
                  </span>
                </div>
                
                {d.winning_script && (
                  <div style={{ position: 'relative', backgroundColor: 'var(--bg-color)', padding: '1.5rem', borderRadius: '12px', boxShadow: 'var(--neo-shadow-sm)', marginTop: '1rem' }}>
                    <p style={{ fontStyle: 'italic', fontSize: '1.05rem', color: 'var(--text-ink)', paddingRight: '2rem' }}>"{d.winning_script}"</p>
                    <button 
                      onClick={() => handleCopy(d.winning_script)}
                      title="Copy script"
                      style={{ 
                        position: 'absolute',
                        top: '1rem',
                        right: '1rem',
                        background: 'transparent',
                        border: 'none',
                        cursor: 'pointer',
                        color: 'var(--accent-color)',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        width: '32px',
                        height: '32px',
                        borderRadius: '50%',
                        boxShadow: 'var(--neo-shadow-sm)',
                        backgroundColor: 'var(--bg-color)'
                      }}
                    >
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                        <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
                      </svg>
                    </button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* FULL PROSE — collapsed by default; the banner and bullets above carry the summary */}
      {score_narrative && (
        <details className="neo-card">
          <summary style={{ cursor: 'pointer', fontSize: '1.1rem', fontWeight: 700, color: 'var(--text-ink)' }}>
            Read the full score narrative
          </summary>
          <p style={{ whiteSpace: 'pre-wrap', lineHeight: 1.6, marginTop: '1rem', maxWidth: '75ch' }}>
            {cleanProse(score_narrative)}
          </p>
        </details>
      )}
      {financial_advice && (
        <details className="neo-card">
          <summary style={{ cursor: 'pointer', fontSize: '1.1rem', fontWeight: 700, color: 'var(--text-ink)' }}>
            Read the full financial assessment
          </summary>
          <p style={{ whiteSpace: 'pre-wrap', lineHeight: 1.6, marginTop: '1rem', maxWidth: '75ch' }}>
            {cleanProse(financial_advice)}
          </p>
        </details>
      )}
    </div>
  );
}

import { useEffect, useState } from 'react';

interface FinalReportProps {
  report: any;
}

const ScoreDial = ({ score }: { score: number }) => {
  const [animatedScore, setAnimatedScore] = useState(0);

  useEffect(() => {
    let current = 0;
    const interval = setInterval(() => {
      if (current >= score) {
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
  
  let color = '#2ed573'; // Green
  if (animatedScore < 50) color = '#ff4757'; // Red
  else if (animatedScore < 75) color = '#f5c518'; // Yellow

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
  if (!report || !report.arena_result || !report.arena_result.counterfactual) return null;

  const { arena_result, score_result } = report;
  const { debates, counterfactual: cf } = arena_result;
  const factors = score_result?.factors || {};

  const handleCopy = (text: string) => {
    navigator.clipboard.writeText(text);
    alert('Copied to clipboard!');
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '2rem' }}>
      
      {/* ARENA COUNTERFACTUAL BANNER (LOUDEST ELEMENT) */}
      <div className="neo-card" style={{ 
        borderLeft: '8px solid var(--accent-color)', 
        background: 'linear-gradient(135deg, var(--bg-color), #fdfdfd)',
        padding: '2rem',
        textAlign: 'center'
      }}>
        <h2 style={{ fontSize: '2rem', marginBottom: '0.5rem', color: 'var(--accent-color)' }}>
          Win these {cf.items_removed} items
        </h2>
        <p style={{ fontSize: '1.25rem', fontWeight: 600 }}>
          Score: {cf.original_score} → <span style={{ color: 'var(--success-color)', fontSize: '1.5rem' }}>{cf.new_score}</span>
        </p>
        <p style={{ fontSize: '1.5rem', fontWeight: 800, marginTop: '1rem' }}>
          Save <span style={{ color: 'var(--success-color)' }}>${cf.savings_amount.toLocaleString()}</span>
        </p>
      </div>

      <div className="neo-card" style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
        <h3 style={{ textAlign: 'center', marginBottom: '1rem' }}>Overall Deal Score</h3>
        <ScoreDial score={cf.original_score} />
        
        {/* Factor Breakdown Bars */}
        <div style={{ width: '100%', marginTop: '2rem' }}>
          <h4 style={{ marginBottom: '1rem', fontSize: '1rem', color: '#8a96a8', textTransform: 'uppercase' }}>Score Factors</h4>
          {Object.entries(factors).map(([key, value]: [string, any]) => (
            <div key={key} style={{ marginBottom: '1rem' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.25rem', fontSize: '0.85rem', fontWeight: 600 }}>
                <span>{key.replace(/_/g, ' ').toUpperCase()}</span>
                <span>{value}/100</span>
              </div>
              <div style={{ width: '100%', height: '8px', backgroundColor: 'var(--bg-color)', borderRadius: '4px', boxShadow: 'var(--neo-inset)' }}>
                <div style={{ 
                  width: `${value}%`, 
                  height: '100%', 
                  backgroundColor: value > 75 ? 'var(--success-color)' : (value > 50 ? 'var(--accent-color)' : 'var(--error-color)'), 
                  borderRadius: '4px' 
                }} />
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* FLAGS & SCRIPTS */}
      <div className="neo-card">
        <h3 style={{ marginBottom: '1.5rem' }}>Negotiation Playbook</h3>
        {debates.map((d: any, idx: number) => {
          const isWin = d.resolution === 'buyer_concession' || d.resolution === 'waived';
          return (
            <div key={idx} className="neo-card-recessed" style={{ 
              marginBottom: '1rem', 
              borderLeft: `6px solid ${isWin ? 'var(--success-color)' : 'var(--error-color)'}` 
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                <div>
                  <h4 style={{ fontSize: '1.1rem', marginBottom: '0.5rem' }}>{d.flag_id.replace(/_/g, ' ').toUpperCase()}</h4>
                  <p style={{ fontSize: '0.9rem', color: '#666', marginBottom: '1rem' }}>
                    <strong>Citation:</strong> {d.compliance_citation || 'N/A'}
                  </p>
                </div>
                <span style={{ 
                  padding: '4px 8px', 
                  borderRadius: '12px', 
                  fontSize: '0.8rem', 
                  fontWeight: 'bold',
                  backgroundColor: isWin ? 'var(--success-color)' : 'var(--error-color)',
                  color: '#fff'
                }}>
                  {d.resolution.replace(/_/g, ' ').toUpperCase()}
                </span>
              </div>
              
              <div style={{ backgroundColor: 'var(--bg-color)', padding: '1rem', borderRadius: '8px', boxShadow: 'var(--neo-shadow-sm)' }}>
                <p style={{ fontStyle: 'italic', marginBottom: '1rem' }}>"{d.suggested_script}"</p>
                <button 
                  onClick={() => handleCopy(d.suggested_script)}
                  className="neo-button" 
                  style={{ fontSize: '0.85rem', padding: '8px 16px' }}
                >
                  Copy Script
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

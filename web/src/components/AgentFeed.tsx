import { useEffect, useRef, useState } from 'react';
import type { AgentStepEvent } from '../types';

interface AgentFeedProps {
  events: AgentStepEvent[];
}

const AGENT_NAMES: Record<string, string> = {
  extraction: 'Extractor',
  market: 'Market Research',
  compliance: 'Compliance',
  scoring_narrator: 'Deal Scorer',
  negotiation_arena: 'Negotiator',
  financial_advisor: 'Advisor',
  dealer: 'Dealer',
  buyer_coach: 'Coach',
  referee: 'Referee'
};

const getAgentColor = (agent: string) => {
  if (agent === 'dealer') return 'var(--error-color)';
  if (agent === 'buyer_coach' || agent === 'financial_advisor') return 'var(--success-color)';
  if (agent === 'referee' || agent === 'scoring_narrator') return 'var(--accent-color)';
  return '#8a96a8';
};

export function AgentFeed({ events }: AgentFeedProps) {
  const feedEndRef = useRef<HTMLDivElement>(null);
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);

  useEffect(() => {
    feedEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [events]);

  const toggleExpand = (idx: number) => {
    setExpandedIdx(prev => prev === idx ? null : idx);
  };

  return (
    <div className="neo-card-recessed" style={{ height: '600px', overflowY: 'auto', padding: '1.5rem', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
      {events.map((ev, idx) => {
        const isArena = ['dealer', 'buyer_coach', 'referee'].includes(ev.agent);
        const agentName = AGENT_NAMES[ev.agent] || ev.agent || 'SYSTEM';
        const isExpanded = expandedIdx === idx;
        
        if (isArena && ev.kind === 'text') {
          // Render as chat bubble
          const isDealer = ev.agent === 'dealer';
          return (
            <div key={idx} style={{
              alignSelf: isDealer ? 'flex-end' : 'flex-start',
              maxWidth: '80%',
              backgroundColor: 'var(--bg-color)',
              boxShadow: 'var(--neo-shadow-sm)',
              borderRadius: '16px',
              borderBottomRightRadius: isDealer ? '4px' : '16px',
              borderBottomLeftRadius: !isDealer ? '4px' : '16px',
              padding: '1rem',
              borderLeft: !isDealer ? `4px solid ${getAgentColor(ev.agent)}` : 'none',
              borderRight: isDealer ? `4px solid ${getAgentColor(ev.agent)}` : 'none',
              cursor: 'pointer'
            }} onClick={() => toggleExpand(idx)}>
              <div style={{ fontSize: '0.75rem', fontWeight: 600, color: getAgentColor(ev.agent), marginBottom: '0.25rem', textTransform: 'uppercase' }}>
                {agentName}
              </div>
              <div style={{ fontSize: '0.9rem' }}>
                {isExpanded ? ev.summary : (ev.summary.length > 90 ? ev.summary.substring(0, 90) + '...' : ev.summary)}
              </div>
              {isExpanded && (
                <div style={{ marginTop: '1rem', padding: '1rem', backgroundColor: '#f1f3f6', borderRadius: '8px', boxShadow: 'var(--neo-inset)', fontFamily: 'monospace', fontSize: '0.8rem', whiteSpace: 'pre-wrap', color: 'var(--text-ink)', overflowX: 'auto' }}>
                  {JSON.stringify(ev, null, 2)}
                </div>
              )}
            </div>
          );
        }

        // Default event row
        return (
          <div key={idx} onClick={() => toggleExpand(idx)} style={{
            display: 'flex',
            flexDirection: 'column',
            gap: '0.5rem',
            padding: '1rem',
            backgroundColor: 'var(--bg-color)',
            boxShadow: 'var(--neo-shadow-sm)',
            borderRadius: '12px',
            cursor: 'pointer'
          }}>
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: '1rem' }}>
              <div style={{
                backgroundColor: 'var(--bg-color)',
                boxShadow: 'var(--neo-inset)',
                padding: '4px 12px',
                borderRadius: '20px',
                fontSize: '0.75rem',
                fontWeight: 600,
                color: 'var(--text-ink)',
                minWidth: '120px',
                textAlign: 'center',
                textTransform: 'uppercase'
              }}>
                {agentName}
              </div>
              
              <div style={{ flex: 1, fontSize: '0.9rem' }}>
                <span style={{ 
                  fontWeight: 600, 
                  color: '#8a96a8', 
                  marginRight: '0.5rem',
                  fontSize: '0.75rem',
                  textTransform: 'uppercase',
                  padding: '2px 6px',
                  border: '1px solid #a3b1c6',
                  borderRadius: '4px'
                }}>
                  {ev.kind.replace('_', ' ')}
                </span>
                {isExpanded ? ev.summary : (ev.summary.length > 90 ? ev.summary.substring(0, 90) + '...' : ev.summary)}
              </div>
            </div>
            
            {isExpanded && (
              <div style={{ marginTop: '0.5rem', padding: '1rem', backgroundColor: '#f1f3f6', borderRadius: '8px', boxShadow: 'var(--neo-inset)', fontFamily: 'monospace', fontSize: '0.8rem', whiteSpace: 'pre-wrap', color: 'var(--text-ink)', overflowX: 'auto' }}>
                {JSON.stringify(ev, null, 2)}
              </div>
            )}
          </div>
        );
      })}
      
      <div ref={feedEndRef} />
    </div>
  );
}

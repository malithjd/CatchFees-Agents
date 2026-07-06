import { useEffect, useRef } from 'react';
import type { AgentStepEvent } from '../types';

interface AgentFeedProps {
  events: AgentStepEvent[];
}

export function AgentFeed({ events }: AgentFeedProps) {
  const feedEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    feedEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [events]);

  const getAgentColor = (agent: string) => {
    if (agent === 'dealer') return '#ff4757';
    if (agent === 'buyer_coach') return '#2ed573';
    if (agent === 'referee') return '#f5c518';
    return '#8a96a8';
  };

  return (
    <div className="neo-card-recessed" style={{ height: '600px', overflowY: 'auto', padding: '1.5rem', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
      {events.map((ev, idx) => {
        const isArena = ['dealer', 'buyer_coach', 'referee'].includes(ev.agent);
        
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
            }}>
              <div style={{ fontSize: '0.75rem', fontWeight: 600, color: getAgentColor(ev.agent), marginBottom: '0.25rem', textTransform: 'uppercase' }}>
                {ev.agent.replace('_', ' ')}
              </div>
              <div style={{ fontSize: '0.9rem' }}>{ev.summary}</div>
            </div>
          );
        }

        // Default event row
        return (
          <div key={idx} style={{
            display: 'flex',
            alignItems: 'flex-start',
            gap: '1rem',
            padding: '1rem',
            backgroundColor: 'var(--bg-color)',
            boxShadow: 'var(--neo-shadow-sm)',
            borderRadius: '12px',
          }}>
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
              {ev.agent || 'SYSTEM'}
            </div>
            
            <div style={{ flex: 1, fontSize: '0.9rem' }}>
              <span style={{ fontWeight: 600, color: '#8a96a8', marginRight: '0.5rem' }}>[{ev.kind}]</span>
              {ev.summary}
            </div>
          </div>
        );
      })}
      
      <div ref={feedEndRef} />
    </div>
  );
}

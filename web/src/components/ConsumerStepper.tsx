
import type { AgentStepEvent } from '../types';

interface ConsumerStepperProps {
  events: AgentStepEvent[];
  isDone: boolean;
}

const STAGES = [
  { id: 1, label: 'Reading your documents', agents: ['extraction'] },
  { id: 2, label: 'Checking market & state law', agents: ['market', 'compliance'] },
  { id: 3, label: 'Scoring the deal', agents: ['scoring_narrator'] },
  { id: 4, label: 'Negotiating on your behalf', agents: ['negotiation_arena', 'dealer', 'buyer_coach', 'referee'] },
  { id: 5, label: 'Preparing advice', agents: ['financial_advisor'] }
];

export function ConsumerStepper({ events, isDone }: ConsumerStepperProps) {
  // Determine current stage based on the last event's agent
  let currentStageId = 1;
  
  if (isDone) {
    currentStageId = 6; // All completed
  } else if (events.length > 0) {
    const lastEvent = events[events.length - 1];
    const stage = STAGES.find(s => s.agents.includes(lastEvent.agent));
    if (stage) {
      currentStageId = stage.id;
    } else {
      // If agent not strictly mapped, try to find highest stage we reached
      // by looking backwards
      for (let i = events.length - 1; i >= 0; i--) {
        const s = STAGES.find(s => s.agents.includes(events[i].agent));
        if (s) {
          currentStageId = s.id;
          break;
        }
      }
    }
  }

  return (
    <div className="neo-card-recessed" style={{ padding: '2rem' }}>
      <h3 style={{ marginBottom: '2rem', textAlign: 'center', color: 'var(--text-ink)' }}>Analyzing Your Deal</h3>
      
      <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
        {STAGES.map((stage) => {
          const isActive = currentStageId === stage.id && !isDone;
          const isCompleted = currentStageId > stage.id || isDone;
          const isPending = currentStageId < stage.id;

          return (
            <div key={stage.id} style={{ 
              display: 'flex', 
              alignItems: 'center', 
              gap: '1rem',
              opacity: isPending ? 0.5 : 1,
              transition: 'opacity 0.3s'
            }}>
              <div style={{
                width: '32px',
                height: '32px',
                borderRadius: '50%',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                backgroundColor: isCompleted ? 'var(--success-color)' : (isActive ? 'var(--accent-color)' : 'var(--bg-color)'),
                color: isCompleted || isActive ? '#fff' : 'var(--text-ink)',
                boxShadow: isCompleted || isActive ? 'var(--neo-shadow-sm)' : 'var(--neo-inset)',
                fontWeight: 'bold',
                position: 'relative'
              }}>
                {isCompleted ? '✓' : stage.id}
                {isActive && (
                  <div style={{
                    position: 'absolute',
                    top: '-4px', left: '-4px', right: '-4px', bottom: '-4px',
                    borderRadius: '50%',
                    border: '2px solid var(--accent-color)',
                    animation: 'pulse 1.5s infinite',
                    pointerEvents: 'none'
                  }} />
                )}
              </div>
              <div style={{ 
                flex: 1, 
                fontSize: '1.1rem', 
                fontWeight: isActive ? 700 : 500,
                color: isActive ? 'var(--text-ink)' : (isCompleted ? '#5a6678' : '#8a96a8')
              }}>
                {stage.label}
              </div>
              {isActive && (
                <div className="shimmer" style={{ width: '60px', height: '8px', borderRadius: '4px' }} />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

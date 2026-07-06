import { useState } from 'react';
import { LandingHero } from './components/LandingHero';
import { UploadPanel } from './components/UploadPanel';
import { AgentFeed } from './components/AgentFeed';
import { FinalReport } from './components/FinalReport';
import { QuarantineBanner } from './components/QuarantineBanner';
import type { AppEvent, AgentStepEvent } from './types';

function App() {
  const [events, setEvents] = useState<AgentStepEvent[]>([]);
  const [report, setReport] = useState<any>(null);
  const [quarantineReason, setQuarantineReason] = useState<string | null>(null);
  const [isProcessing, setIsProcessing] = useState(false);

  const handleClear = () => {
    setEvents([]);
    setReport(null);
    setQuarantineReason(null);
    setIsProcessing(true);
  };

  const handleEvent = (event: AppEvent) => {
    if (event.type === 'agent_step') {
      setEvents(prev => [...prev, event]);
    } else if (event.type === 'done') {
      setReport(event.report);
      setIsProcessing(false);
    } else if (event.type === 'quarantined') {
      setQuarantineReason(event.reason);
      setIsProcessing(false);
    }
  };

  return (
    <>
      <LandingHero />

      <main className="container" style={{ paddingBottom: '4rem' }}>
        {quarantineReason && (
          <QuarantineBanner reason={quarantineReason} />
        )}

        <div className="app-section">
          <div style={{ display: 'flex', flexDirection: 'column', gap: '2rem' }}>
            <UploadPanel 
              onEvent={handleEvent} 
              onClear={handleClear} 
              isProcessing={isProcessing} 
            />
            
            {events.length > 0 && (
              <div className="neo-card">
                <h3 style={{ marginBottom: '1rem' }}>Live Agent Feed</h3>
                <AgentFeed events={events} />
              </div>
            )}
          </div>

          <div>
            {report && (
              <FinalReport report={report} />
            )}
            
            {!report && events.length > 0 && !quarantineReason && (
              <div className="neo-card-recessed" style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: '400px' }}>
                <p style={{ color: '#8a96a8', fontStyle: 'italic' }}>
                  Agents are analyzing the deal... Waiting for final report...
                </p>
              </div>
            )}
          </div>
        </div>
      </main>
    </>
  );
}

export default App;

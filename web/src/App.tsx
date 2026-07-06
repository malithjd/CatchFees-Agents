import { useState } from 'react';
import { LandingHero } from './components/LandingHero';
import { UploadPanel } from './components/UploadPanel';
import { AgentFeed } from './components/AgentFeed';
import { FinalReport } from './components/FinalReport';
import { QuarantineBanner } from './components/QuarantineBanner';
import { ConsumerStepper } from './components/ConsumerStepper';
import type { AppEvent, AgentStepEvent } from './types';

function App() {
  const [events, setEvents] = useState<AgentStepEvent[]>([]);
  const [report, setReport] = useState<any>(null);
  const [quarantineReason, setQuarantineReason] = useState<string | null>(null);
  const [isProcessing, setIsProcessing] = useState(false);
  const [streamError, setStreamError] = useState<string | null>(null);
  const [nerdMode, setNerdMode] = useState(false);

  const handleClear = () => {
    setEvents([]);
    setReport(null);
    setQuarantineReason(null);
    setStreamError(null);
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
    } else if (event.type === 'stream_error') {
      setStreamError(event.message);
      setIsProcessing(false);
    }
  };

  const hasStarted = events.length > 0 || isProcessing || report || quarantineReason || streamError;

  return (
    <>
      <LandingHero />

      <main className="container" style={{ paddingBottom: '4rem' }}>
        <div className="app-section">
          
          <UploadPanel 
            onEvent={handleEvent} 
            onClear={handleClear} 
            isProcessing={isProcessing} 
          />

          {hasStarted && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '2rem' }}>
              <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', cursor: 'pointer', padding: '0.5rem 1rem', borderRadius: '20px', boxShadow: 'var(--neo-shadow-sm)', backgroundColor: 'var(--bg-color)', fontWeight: 600, fontSize: '0.9rem' }}>
                  <input 
                    type="checkbox" 
                    checked={nerdMode} 
                    onChange={(e) => setNerdMode(e.target.checked)}
                    style={{ width: '1.2rem', height: '1.2rem' }}
                  />
                  Show agent activity
                </label>
              </div>

              {quarantineReason && (
                <QuarantineBanner reason={quarantineReason} />
              )}
              
              {!report && !streamError && !quarantineReason && (
                <>
                  {nerdMode ? (
                    <AgentFeed events={events} />
                  ) : (
                    <ConsumerStepper events={events} isDone={false} />
                  )}
                </>
              )}

              {report && (
                <>
                  <FinalReport report={report} />
                  {nerdMode && (
                    <div style={{ marginTop: '2rem' }}>
                      <h3 style={{ marginBottom: '1rem' }}>Full Agent Activity Log</h3>
                      <AgentFeed events={events} />
                    </div>
                  )}
                </>
              )}
              
              {!report && streamError && (
                <div className="neo-card" style={{ borderLeft: '6px solid var(--error-color)' }}>
                  <h3 style={{ color: 'var(--error-color)', marginBottom: '0.5rem' }}>Connection Lost</h3>
                  <p>The analysis stream was interrupted before completion. ({streamError})</p>
                  <button onClick={() => window.scrollTo({top: 0, behavior: 'smooth'})} className="neo-button" style={{ marginTop: '1rem' }}>
                    Retry Analysis
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      </main>
    </>
  );
}

export default App;

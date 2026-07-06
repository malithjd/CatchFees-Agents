

interface QuarantineBannerProps {
  reason: string;
}

export function QuarantineBanner({ reason }: QuarantineBannerProps) {
  return (
    <div style={{
      width: '100%',
      backgroundColor: '#1a1c23', // Dark contrast background
      color: '#ffffff',
      padding: '3rem 2rem',
      borderRadius: 'var(--radius-md)',
      boxShadow: '0 20px 40px rgba(255, 71, 87, 0.3)',
      borderLeft: '12px solid var(--error-color)',
      textAlign: 'center',
      marginBottom: '2rem'
    }}>
      <h2 style={{ fontSize: '2.5rem', color: 'var(--error-color)', marginBottom: '1rem', textTransform: 'uppercase', letterSpacing: '2px' }}>
        ⚠️ Deal Quarantined
      </h2>
      <p style={{ fontSize: '1.25rem', marginBottom: '2rem', maxWidth: '800px', margin: '0 auto 2rem auto', lineHeight: 1.6 }}>
        Our Intake Guard detected suspicious instructions embedded in the deal documents. 
        This is a known attack vector where a malicious party attempts to hijack the AI agents into ignoring fees or giving an artificially high score.
      </p>
      
      <div style={{
        backgroundColor: 'rgba(255, 71, 87, 0.1)',
        padding: '1.5rem',
        borderRadius: '8px',
        border: '1px solid rgba(255, 71, 87, 0.3)',
        display: 'inline-block',
        textAlign: 'left'
      }}>
        <h3 style={{ fontSize: '1rem', color: '#ff7b88', marginBottom: '0.5rem', textTransform: 'uppercase' }}>Technical Reason</h3>
        <code style={{ fontFamily: 'monospace', fontSize: '1.1rem' }}>{reason}</code>
      </div>
      
      <p style={{ marginTop: '2rem', fontSize: '1.1rem', color: '#8a96a8' }}>
        For your safety, the analysis has been aborted. Please verify the source of your document.
      </p>
    </div>
  );
}

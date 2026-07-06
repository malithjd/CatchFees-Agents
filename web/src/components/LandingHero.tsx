

export function LandingHero() {
  const scrollToUpload = () => {
    document.getElementById('upload-panel')?.scrollIntoView({ behavior: 'smooth' });
  };

  return (
    <section style={{
      minHeight: '100vh',
      display: 'flex',
      flexDirection: 'column',
      justifyContent: 'center',
      alignItems: 'center',
      textAlign: 'center',
      padding: '2rem',
      position: 'relative',
      overflow: 'hidden'
    }}>
      {/* Decorative Neumorphic Shape (Embossed Dial Motif) */}
      <div style={{
        position: 'absolute',
        width: '600px',
        height: '600px',
        borderRadius: '50%',
        boxShadow: 'var(--neo-shadow)',
        top: '-10%',
        right: '-10%',
        zIndex: 0,
        opacity: 0.5
      }} />
      <div style={{
        position: 'absolute',
        width: '400px',
        height: '400px',
        borderRadius: '50%',
        boxShadow: 'var(--neo-inset)',
        top: '5%',
        right: '5%',
        zIndex: 0,
        opacity: 0.5
      }} />

      <div style={{ position: 'relative', zIndex: 1, maxWidth: '800px' }}>
        <p style={{ 
          fontSize: '1rem', 
          fontWeight: 600, 
          letterSpacing: '2px', 
          textTransform: 'uppercase', 
          color: '#8a96a8',
          marginBottom: '1.5rem'
        }}>
          CatchFees · Multi-Agent Deal Analyzer
        </p>
        
        <h1 style={{
          fontSize: 'clamp(2.5rem, 7vw, 5rem)',
          fontWeight: 900,
          lineHeight: 1.1,
          letterSpacing: '-0.03em',
          marginBottom: '2rem',
          color: 'var(--text-ink)'
        }}>
          The dealer has a team.<br />Now you do too.
        </h1>
        
        <p style={{
          fontSize: '1.25rem',
          lineHeight: 1.6,
          color: '#5a6678',
          marginBottom: '3rem',
          maxWidth: '600px',
          margin: '0 auto 3rem auto'
        }}>
          Five AI agents read your purchase agreement, check the law in your state, score the deal — then argue with a virtual dealer so you don't have to.
        </p>

        <button 
          onClick={scrollToUpload}
          className="neo-button neo-button-primary"
          style={{
            fontSize: '1.25rem',
            padding: '16px 48px',
            borderRadius: 'var(--radius-lg)',
            marginBottom: '2rem'
          }}
        >
          Analyze my deal
        </button>

        <div style={{
          fontSize: '0.85rem',
          color: '#8a96a8',
          fontWeight: 500,
          marginTop: '1rem'
        }}>
          Built on Google ADK · Legal citations via MCP · Deterministic scoring
        </div>
      </div>
    </section>
  );
}

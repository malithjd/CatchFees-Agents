import { useNavigate } from 'react-router-dom';

export default function Landing() {
  const navigate = useNavigate();

  return (
    <div style={{ minHeight: '100vh', paddingBottom: '4rem' }}>
      {/* Hero Section */}
      <section style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))',
        gap: '4rem',
        alignItems: 'center',
        padding: '4rem 2rem',
        maxWidth: '1200px',
        margin: '0 auto',
        minHeight: '80vh'
      }}>
        <div style={{ position: 'relative', zIndex: 1 }}>
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
            fontSize: 'clamp(2.5rem, 6vw, 4.5rem)',
            fontWeight: 900,
            lineHeight: 1.1,
            letterSpacing: '-0.03em',
            marginBottom: '1.5rem',
            color: 'var(--text-ink)'
          }}>
            The car-buying copilot for introverts.
          </h1>
          
          <p style={{
            fontSize: '1.25rem',
            lineHeight: 1.6,
            color: '#5a6678',
            marginBottom: '3rem',
            maxWidth: '500px'
          }}>
            Hate haggling? Five AI agents read the fine print, check the law in your state, and argue with the dealer — so you never have to say a word.
          </p>

          <button 
            onClick={() => navigate('/analyze')}
            className="neo-button neo-button-primary"
            style={{
              fontSize: '1.25rem',
              padding: '16px 48px',
              borderRadius: 'var(--radius-lg)'
            }}
          >
            Analyze my deal
          </button>
        </div>

        {/* Hero Visual: Mock Report Card */}
        <div className="neo-card" style={{ position: 'relative', padding: '2rem' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', marginBottom: '2rem' }}>
            <div style={{ width: '80px', height: '80px', borderRadius: '50%', background: 'conic-gradient(var(--error-color) 30%, var(--bg-color) 0)', boxShadow: 'var(--neo-inset)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <span style={{ fontSize: '1.5rem', fontWeight: 900 }}>3/10</span>
            </div>
            <div>
              <h3 style={{ fontSize: '1.2rem', marginBottom: '0.2rem' }}>Poor Deal — Walk Away</h3>
              <p style={{ color: '#5a6678', fontSize: '0.9rem' }}>$4,500 in overpriced fees found.</p>
            </div>
          </div>
          
          <div className="neo-card-recessed" style={{ padding: '1rem', marginBottom: '1.5rem', borderLeft: '4px solid var(--error-color)' }}>
            <h4 style={{ color: 'var(--error-color)', marginBottom: '0.5rem' }}>Illegal Doc Fee</h4>
            <p style={{ fontSize: '0.9rem', color: '#5a6678' }}>Dealer charged $599. State cap is $150.</p>
          </div>

          <div className="neo-card" style={{ padding: '1.5rem', backgroundColor: 'var(--accent-color)', color: '#fff' }}>
            <div style={{ fontSize: '0.8rem', textTransform: 'uppercase', fontWeight: 700, marginBottom: '0.5rem', opacity: 0.9 }}>Estimated Savings</div>
            <div style={{ fontSize: '2rem', fontWeight: 900 }}>$2,450</div>
          </div>
        </div>
      </section>

      {/* Trust Row */}
      <section style={{ textAlign: 'center', padding: '2rem', borderTop: '1px solid rgba(0,0,0,0.05)', borderBottom: '1px solid rgba(0,0,0,0.05)' }}>
        <p style={{ fontSize: '0.85rem', color: '#8a96a8', fontWeight: 500, letterSpacing: '1px', textTransform: 'uppercase' }}>
          Built on Google ADK · Legal citations via MCP · Deterministic scoring
        </p>
      </section>

      {/* How it Works Strip */}
      <section style={{ maxWidth: '1200px', margin: '4rem auto', padding: '0 2rem' }}>
        <h2 style={{ textAlign: 'center', fontSize: '2.5rem', marginBottom: '3rem' }}>How it works</h2>
        <div style={{ 
          display: 'grid', 
          gridTemplateColumns: 'repeat(auto-fit, minmax(250px, 1fr))', 
          gap: '2rem' 
        }}>
          <div className="neo-card" style={{ textAlign: 'center', padding: '2rem' }}>
            <div style={{ width: '48px', height: '48px', borderRadius: '50%', boxShadow: 'var(--neo-inset)', margin: '0 auto 1.5rem auto', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '1.5rem', fontWeight: 'bold', color: 'var(--accent-color)' }}>1</div>
            <h3 style={{ marginBottom: '1rem' }}>Upload Document</h3>
            <p style={{ color: '#5a6678', fontSize: '0.95rem' }}>Snap a photo or upload your dealer's quote. Our Vision agent reads the messy fine print.</p>
          </div>
          <div className="neo-card" style={{ textAlign: 'center', padding: '2rem' }}>
            <div style={{ width: '48px', height: '48px', borderRadius: '50%', boxShadow: 'var(--neo-inset)', margin: '0 auto 1.5rem auto', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '1.5rem', fontWeight: 'bold', color: 'var(--accent-color)' }}>2</div>
            <h3 style={{ marginBottom: '1rem' }}>Market & Law Check</h3>
            <p style={{ color: '#5a6678', fontSize: '0.95rem' }}>Agents simultaneously check live market prices and verify state fee limits.</p>
          </div>
          <div className="neo-card" style={{ textAlign: 'center', padding: '2rem' }}>
            <div style={{ width: '48px', height: '48px', borderRadius: '50%', boxShadow: 'var(--neo-inset)', margin: '0 auto 1.5rem auto', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '1.5rem', fontWeight: 'bold', color: 'var(--accent-color)' }}>3</div>
            <h3 style={{ marginBottom: '1rem' }}>Agent Debate</h3>
            <p style={{ color: '#5a6678', fontSize: '0.95rem' }}>A buyer coach argues with a simulated dealer until they get you the best negotiation script.</p>
          </div>
          <div className="neo-card" style={{ textAlign: 'center', padding: '2rem' }}>
            <div style={{ width: '48px', height: '48px', borderRadius: '50%', boxShadow: 'var(--neo-inset)', margin: '0 auto 1.5rem auto', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '1.5rem', fontWeight: 'bold', color: 'var(--accent-color)' }}>4</div>
            <h3 style={{ marginBottom: '1rem' }}>Read the Scripts</h3>
            <p style={{ color: '#5a6678', fontSize: '0.95rem' }}>Use the exact, legally cited words to push back on junk fees and save thousands.</p>
          </div>
        </div>
      </section>
    </div>
  );
}

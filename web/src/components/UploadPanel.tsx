import { useState, useRef } from 'react';
import type { ChangeEvent, DragEvent, FormEvent } from 'react';
import type { AppEvent } from '../types';

interface UploadPanelProps {
  onEvent: (event: AppEvent) => void;
  onClear: () => void;
  isProcessing: boolean;
}

export function UploadPanel({ onEvent, onClear, isProcessing }: UploadPanelProps) {
  const [textOnly, setTextOnly] = useState(false);
  const [details, setDetails] = useState('');
  const [images, setImages] = useState<File[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleFileChange = (e: ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) {
      const selected = Array.from(e.target.files).slice(0, 5);
      setImages(selected);
    }
  };

  const handleDrop = (e: DragEvent) => {
    e.preventDefault();
    if (e.dataTransfer.files) {
      const selected = Array.from(e.dataTransfer.files)
        .filter(f => f.type.startsWith('image/'))
        .slice(0, 5);
      setImages(selected);
    }
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (isProcessing) return;

    onClear();
    
    const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8080';
    let url = '';
    let body: any = null;
    let headers: Record<string, string> = {};

    if (textOnly) {
      if (!details.trim()) {
        alert("Please enter text details.");
        return;
      }
      url = `${API_URL}/analyze_text`;
      body = JSON.stringify({ text: details });
      headers = { 'Content-Type': 'application/json' };
    } else {
      if (images.length === 0 && !details.trim()) {
        alert("Please provide at least one image or text details.");
        return;
      }
      url = `${API_URL}/analyze`;
      const formData = new FormData();
      images.forEach(img => formData.append('images', img));
      if (details.trim()) {
        formData.append('details', details);
      }
      body = formData;
    }

    try {
      const response = await fetch(url, {
        method: 'POST',
        headers,
        body,
      });

      if (!response.body) throw new Error("No response body");

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        
        // Keep the last partial line in the buffer
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const dataStr = line.replace('data: ', '').trim();
            if (dataStr) {
              try {
                const event = JSON.parse(dataStr) as AppEvent;
                onEvent(event);
              } catch (err) {
                console.error("Failed to parse event", dataStr, err);
              }
            }
          }
        }
      }
    } catch (err) {
      console.error(err);
      alert("An error occurred during analysis.");
    }
  };

  return (
    <div className="neo-card" id="upload-panel">
      <h2 style={{ marginBottom: '1rem' }}>Submit Your Deal</h2>
      
      <div style={{ marginBottom: '1.5rem', display: 'flex', alignItems: 'center', gap: '1rem' }}>
        <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', cursor: 'pointer' }}>
          <input 
            type="checkbox" 
            checked={textOnly} 
            onChange={(e) => setTextOnly(e.target.checked)}
            style={{ width: '1.2rem', height: '1.2rem' }}
          />
          Text-only mode
        </label>
      </div>

      <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
        
        {!textOnly && (
          <div 
            className="neo-card-recessed"
            onDragOver={e => e.preventDefault()}
            onDrop={handleDrop}
            style={{ 
              border: '2px dashed #a3b1c6', 
              textAlign: 'center', 
              padding: '2rem',
              cursor: 'pointer'
            }}
            onClick={() => fileInputRef.current?.click()}
          >
            <input 
              type="file" 
              multiple 
              accept="image/*" 
              ref={fileInputRef} 
              style={{ display: 'none' }} 
              onChange={handleFileChange}
            />
            {images.length > 0 ? (
              <p>{images.length} image(s) selected.</p>
            ) : (
              <p>Drag & drop up to 5 images here, or click to browse</p>
            )}
          </div>
        )}

        <div>
          <label style={{ display: 'block', marginBottom: '0.5rem', fontWeight: 600 }}>
            {textOnly ? 'Deal Details (Required)' : 'Financing Details (Optional)'}
          </label>
          <textarea 
            className="neo-input" 
            rows={4} 
            placeholder="e.g. 72 months at 5.9% APR with $2000 down..."
            value={details}
            onChange={(e) => setDetails(e.target.value)}
            style={{ resize: 'vertical' }}
          />
        </div>

        <button 
          type="submit" 
          className="neo-button neo-button-primary"
          disabled={isProcessing}
        >
          {isProcessing ? 'Analyzing...' : 'Analyze Deal'}
        </button>

      </form>
    </div>
  );
}

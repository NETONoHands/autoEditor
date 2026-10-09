import { useEffect, useRef } from 'react';

interface ProcessingTerminalProps {
  logs: string[];
  isExpanded: boolean;
  onToggle: () => void;
}

export default function ProcessingTerminal({ logs, isExpanded, onToggle }: ProcessingTerminalProps) {
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (isExpanded && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [logs, isExpanded]);

  return (
    <div className="terminal-container">
      <div className="terminal-header" onClick={onToggle}>
        <span>Detalhes do Processamento</span>
        <small style={{ color: 'var(--muted)', fontSize: '10px' }}>
          {isExpanded ? 'COLAPSAR ▲' : 'EXPANDIR ▼'}
        </small>
      </div>
      
      {isExpanded && (
        <div className="terminal-window" ref={scrollRef}>
          {logs.length === 0 ? (
            <span className="terminal-line" style={{ color: 'var(--muted)', fontStyle: 'italic' }}>
              A aguardar logs do sistema...
            </span>
          ) : (
            logs.map((log, index) => (
              <span key={index} className="terminal-line">
                {log}
              </span>
            ))
          )}
        </div>
      )}
    </div>
  );
}

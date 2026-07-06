export interface AgentStepEvent {
  type: 'agent_step';
  agent: string;
  kind: 'model_call' | 'tool_call' | 'tool_result' | 'text' | 'user_message';
  summary: string;
}

export interface DoneEvent {
  type: 'done';
  report: any; // ArenaResult
}

export interface QuarantineEvent {
  type: 'quarantined';
  reason: string;
}

export interface StreamErrorEvent {
  type: 'stream_error';
  message: string;
}

export type AppEvent = AgentStepEvent | DoneEvent | QuarantineEvent | StreamErrorEvent;

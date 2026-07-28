type FrameHandler = (frame: { type: string; data: any }) => void;

export class AgentSocket {
  private ws: WebSocket | null = null;
  private handlers: Set<FrameHandler> = new Set();
  private reconnectAttempt = 0;
  private closedByUser = false;
  private subscribedConversations: Set<string> = new Set();

  connect() {
    this.closedByUser = false;
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    this.ws = new WebSocket(`${scheme}://${location.host}/ws/agent`);

    this.ws.onopen = () => {
      this.reconnectAttempt = 0;
      this.subscribedConversations.forEach((id) => {
        this.send({ type: "subscribe", data: { conversation_id: id } });
      });
    };

    this.ws.onmessage = (event) => {
      const frame = JSON.parse(event.data);
      this.handlers.forEach((h) => h(frame));
    };

    this.ws.onclose = () => {
      if (this.closedByUser) return;
      const delay = Math.min(1000 * 2 ** this.reconnectAttempt, 15000) + Math.random() * 500;
      this.reconnectAttempt++;
      setTimeout(() => this.connect(), delay);
    };
  }

  send(frame: { type: string; data: any }) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(frame));
    }
  }

  subscribe(conversationId: string) {
    this.subscribedConversations.add(conversationId);
    this.send({ type: "subscribe", data: { conversation_id: conversationId } });
  }

  sync(conversationId: string, sinceSeq: number) {
    this.send({ type: "sync", data: { conversation_id: conversationId, since_seq: sinceSeq } });
  }

  sendMessage(conversationId: string, body: string, clientMsgId: string) {
    this.send({ type: "message.send", data: { conversation_id: conversationId, body, client_msg_id: clientMsgId } });
  }

  typing(conversationId: string, isTyping: boolean) {
    this.send({ type: "typing", data: { conversation_id: conversationId, is_typing: isTyping } });
  }

  markRead(conversationId: string, seq: number) {
    this.send({ type: "read", data: { conversation_id: conversationId, seq } });
  }

  onFrame(handler: FrameHandler): () => void {
    this.handlers.add(handler);
    return () => this.handlers.delete(handler);
  }

  close() {
    this.closedByUser = true;
    this.ws?.close();
  }
}

export const agentSocket = new AgentSocket();

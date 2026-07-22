/* MAOP Dashboard — Chat UI Component */

(function(){
  const MAOP = window.MAOP || (window.MAOP = {});

  MAOP.chat = {
    sessionId: '',
    messages: [],
    streaming: false,

    init(){
      this.sessionId = 'chat-' + Date.now().toString(36);
      this.messages = [];
      this.render();
    },

    render(){
      const panel = document.getElementById('chat-panel');
      if(!panel) return;
      panel.innerHTML = `
        <div class="chat-container">
          <div class="chat-header">
            <h3>MAOP Chat</h3>
            <div class="chat-controls">
              <select id="chat-model" class="chat-select">
                <option value="">Default Agent</option>
              </select>
              <button class="btn btn-sm" data-action="chatClear">Clear</button>
            </div>
          </div>
          <div id="chat-messages" class="chat-messages"></div>
          <div class="chat-input-area">
            <textarea id="chat-input" class="chat-input" placeholder="Type a message..." rows="2"></textarea>
            <button id="chat-send" class="btn btn-primary" data-action="chatSend">Send</button>
          </div>
        </div>
      `;
      this.loadModels();
      this.bindEvents();
    },

    bindEvents(){
      const input = document.getElementById('chat-input');
      if(input){
        input.addEventListener('keydown', e => {
          if(e.key === 'Enter' && !e.shiftKey){
            e.preventDefault();
            this.send();
          }
        });
      }
    },

    async loadModels(){
      try{
        const resp = await fetch('/api/chat/models');
        const data = await resp.json();
        if(data.status === 'ok'){
          const sel = document.getElementById('chat-model');
          if(sel){
            data.data.models.forEach(m => {
              const opt = document.createElement('option');
              opt.value = m.name;
              opt.textContent = `${m.name} (${m.provider})`;
              sel.appendChild(opt);
            });
          }
        }
      }catch(e){ console.warn('Failed to load models:', e); }
    },

    async send(){
      const input = document.getElementById('chat-input');
      const modelSel = document.getElementById('chat-model');
      if(!input || !input.value.trim() || this.streaming) return;

      const msg = input.value.trim();
      const model = modelSel ? modelSel.value : '';
      input.value = '';

      this.addMessage('user', msg);
      this.streaming = true;

      const container = document.getElementById('chat-messages');
      const asstDiv = this.addMessage('assistant', '');
      const contentDiv = asstDiv.querySelector('.chat-content');

      try{
        const resp = await fetch('/api/chat/stream', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            session_id: this.sessionId,
            message: msg,
            model: model,
            stream: true,
          }),
        });

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while(true){
          const {done, value} = await reader.read();
          if(done) break;
          buffer += decoder.decode(value, {stream: true});

          const lines = buffer.split('\n');
          buffer = lines.pop() || '';

          for(const line of lines){
            if(line.startsWith('data: ')){
              try{
                const data = JSON.parse(line.slice(6));
                if(data.content){
                  contentDiv.textContent += data.content;
                  container.scrollTop = container.scrollHeight;
                }
              }catch(e){}
            }
          }
        }
      }catch(e){
        contentDiv.textContent = 'Error: ' + e.message;
      }

      this.streaming = false;
    },

    addMessage(role, content){
      const container = document.getElementById('chat-messages');
      if(!container) return null;

      const div = document.createElement('div');
      div.className = `chat-message chat-${role}`;
      div.innerHTML = `
        <div class="chat-avatar">${role === 'user' ? '👤' : '🤖'}</div>
        <div class="chat-content">${this.escapeHtml(content)}</div>
      `;
      container.appendChild(div);
      container.scrollTop = container.scrollHeight;
      return div;
    },

    clear(){
      this.sessionId = 'chat-' + Date.now().toString(36);
      this.messages = [];
      const container = document.getElementById('chat-messages');
      if(container) container.innerHTML = '';
    },

    escapeHtml(text){
      const d = document.createElement('div');
      d.textContent = text;
      return d.innerHTML;
    }
  };

  document.addEventListener('DOMContentLoaded', () => MAOP.chat.init());
})();
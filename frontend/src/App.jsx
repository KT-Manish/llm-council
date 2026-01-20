import { useState, useEffect } from 'react';
import Sidebar from './components/Sidebar';
import ChatInterface from './components/ChatInterface';
import { api } from './api';
import './App.css';

function App() {
  const [conversations, setConversations] = useState([]);
  const [currentConversationId, setCurrentConversationId] = useState(null);
  const [currentConversation, setCurrentConversation] = useState(null);
  const [isLoading, setIsLoading] = useState(false);

  // Load conversations on mount
  useEffect(() => {
    loadConversations();
  }, []);

  // Load conversation details when selected
  useEffect(() => {
    if (currentConversationId) {
      loadConversation(currentConversationId);
    }
  }, [currentConversationId]);

  const loadConversations = async () => {
    try {
      const convs = await api.listConversations();
      setConversations(convs);
    } catch (error) {
      console.error('Failed to load conversations:', error);
    }
  };

  const loadConversation = async (id) => {
    try {
      const conv = await api.getConversation(id);
      setCurrentConversation(conv);
    } catch (error) {
      console.error('Failed to load conversation:', error);
    }
  };

  const handleNewConversation = async () => {
    try {
      const newConv = await api.createConversation();
      setConversations([
        { id: newConv.id, created_at: newConv.created_at, message_count: 0 },
        ...conversations,
      ]);
      setCurrentConversationId(newConv.id);
    } catch (error) {
      console.error('Failed to create conversation:', error);
    }
  };

  const handleSelectConversation = (id) => {
    setCurrentConversationId(id);
  };

  // Helper to create assistant message shell
  const createAssistantMessageShell = () => ({
    role: 'assistant',
    stage1: null,
    stage2: null,
    stage3: null,
    metadata: null,
    loading: {
      stage1: false,
      stage2: false,
      stage3: false,
    },
  });

  // Helper to handle stage events (shared between text and voice)
  const handleStageEvent = (eventType, data, metadata) => {
    console.log('[App] Stage event:', eventType);

    // Helper to safely update the last assistant message
    const updateLastAssistantMessage = (updater) => {
      setCurrentConversation((prev) => {
        if (!prev || !prev.messages || prev.messages.length === 0) {
          console.warn('[App] No conversation or messages to update');
          return prev;
        }
        const messages = [...prev.messages];
        const lastMsg = messages[messages.length - 1];
        if (!lastMsg || lastMsg.role !== 'assistant') {
          console.warn('[App] Last message is not an assistant message');
          return prev;
        }
        updater(lastMsg);
        return { ...prev, messages };
      });
    };

    switch (eventType) {
      case 'stage1_start':
        updateLastAssistantMessage((msg) => {
          msg.loading.stage1 = true;
        });
        break;

      case 'stage1_complete':
        updateLastAssistantMessage((msg) => {
          msg.stage1 = data;
          msg.loading.stage1 = false;
        });
        break;

      case 'stage2_start':
        updateLastAssistantMessage((msg) => {
          msg.loading.stage2 = true;
        });
        break;

      case 'stage2_complete':
        updateLastAssistantMessage((msg) => {
          msg.stage2 = data;
          msg.metadata = metadata;
          msg.loading.stage2 = false;
        });
        break;

      case 'stage3_start':
        updateLastAssistantMessage((msg) => {
          msg.loading.stage3 = true;
        });
        break;

      case 'stage3_complete':
        updateLastAssistantMessage((msg) => {
          msg.stage3 = data;
          msg.loading.stage3 = false;
        });
        break;

      case 'title_complete':
        loadConversations();
        break;

      case 'complete':
        loadConversations();
        setIsLoading(false);
        break;

      case 'error':
        console.error('[App] Stream error:', data);
        setIsLoading(false);
        break;
    }
  };

  // Handle voice transcription - add user message and prepare for stages
  const handleVoiceTranscription = (text) => {
    if (!text) return;
    console.log('[App] Voice transcription received:', text);

    // Add both user message and assistant shell in single update to avoid race conditions
    const userMessage = { role: 'user', content: text };
    const assistantMessage = createAssistantMessageShell();

    setCurrentConversation((prev) => {
      if (!prev) return prev;
      return {
        ...prev,
        messages: [...prev.messages, userMessage, assistantMessage],
      };
    });

    setIsLoading(true);
  };

  // Handle voice stage updates
  const handleVoiceStageUpdate = (eventType, data, metadata) => {
    if (eventType === 'audio_complete') {
      loadConversations();
      setIsLoading(false);
      return;
    }
    handleStageEvent(eventType, data, metadata);
  };

  const handleSendMessage = async (content) => {
    if (!currentConversationId) return;

    setIsLoading(true);
    try {
      // Optimistically add user message to UI
      const userMessage = { role: 'user', content };
      setCurrentConversation((prev) => ({
        ...prev,
        messages: [...prev.messages, userMessage],
      }));

      // Add the partial assistant message
      setCurrentConversation((prev) => ({
        ...prev,
        messages: [...prev.messages, createAssistantMessageShell()],
      }));

      // Send message with streaming
      await api.sendMessageStream(currentConversationId, content, (eventType, event) => {
        handleStageEvent(eventType, event?.data, event?.metadata);
      });
    } catch (error) {
      console.error('Failed to send message:', error);
      // Remove optimistic messages on error
      setCurrentConversation((prev) => ({
        ...prev,
        messages: prev.messages.slice(0, -2),
      }));
      setIsLoading(false);
    }
  };

  return (
    <div className="app">
      <Sidebar
        conversations={conversations}
        currentConversationId={currentConversationId}
        onSelectConversation={handleSelectConversation}
        onNewConversation={handleNewConversation}
      />
      <ChatInterface
        conversation={currentConversation}
        onSendMessage={handleSendMessage}
        isLoading={isLoading}
        onVoiceTranscription={handleVoiceTranscription}
        onVoiceStageUpdate={handleVoiceStageUpdate}
      />
    </div>
  );
}

export default App;

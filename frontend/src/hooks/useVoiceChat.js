import { useState, useRef, useCallback, useEffect } from 'react';
import { api } from '../api';

export default function useVoiceChat(conversationId, onStageUpdate) {
  const [isRecording, setIsRecording] = useState(false);
  const [isProcessing, setIsProcessing] = useState(false);
  const [transcription, setTranscription] = useState('');
  const [error, setError] = useState(null);

  const wsRef = useRef(null);
  const mediaStreamRef = useRef(null);
  const audioContextRef = useRef(null);
  const processorRef = useRef(null);
  const audioChunksRef = useRef([]);
  const isProcessingRef = useRef(false); // Track processing state in ref to avoid closure issues

  // Cleanup on unmount - but don't close if processing
  useEffect(() => {
    return () => {
      cleanupAudio();
      // Only close WebSocket if not processing
      if (!isProcessingRef.current && wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, []);

  const cleanupAudio = () => {
    if (processorRef.current) {
      processorRef.current.disconnect();
      processorRef.current = null;
    }

    if (audioContextRef.current) {
      try {
        audioContextRef.current.close();
      } catch (e) {
        // Ignore
      }
      audioContextRef.current = null;
    }

    if (mediaStreamRef.current) {
      mediaStreamRef.current.getTracks().forEach(track => track.stop());
      mediaStreamRef.current = null;
    }
  };

  const connectWebSocket = useCallback(() => {
    return new Promise((resolve, reject) => {
      if (!conversationId) {
        reject(new Error('No conversation selected'));
        return;
      }

      // Close existing connection if any
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }

      const ws = new WebSocket(api.getVoiceWebSocketUrl(conversationId));

      ws.onopen = () => {
        console.log('[VoiceChat] WebSocket connected');
        wsRef.current = ws;
        resolve(ws);
      };

      ws.onerror = (e) => {
        console.error('[VoiceChat] WebSocket error:', e);
        reject(new Error('WebSocket connection failed'));
      };

      ws.onclose = (e) => {
        console.log('[VoiceChat] WebSocket closed, code:', e.code, 'reason:', e.reason);
        wsRef.current = null;

        // If closed unexpectedly during processing, show error
        if (isProcessingRef.current) {
          setError('Connection lost during processing');
          setIsProcessing(false);
          isProcessingRef.current = false;
        }

        if (e.code === 4001) {
          setError('Authentication required or OpenAI API key not configured.');
        } else if (e.code === 4003) {
          setError('Access denied');
        } else if (e.code === 4004) {
          setError('Conversation not found');
        }
      };

      ws.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data);
          handleMessage(message);
        } catch (e) {
          console.error('[VoiceChat] Error parsing message:', e);
        }
      };
    });
  }, [conversationId]);

  const handleMessage = (message) => {
    const { type, data, metadata, text } = message;
    console.log('[VoiceChat] Received:', type);

    switch (type) {
      case 'recording_started':
        setIsRecording(true);
        break;

      case 'transcription':
        console.log('[VoiceChat] Transcription:', text);
        setTranscription(text || '');
        setIsProcessing(true);
        isProcessingRef.current = true;
        // Notify parent about transcription
        if (onStageUpdate) {
          onStageUpdate('transcription', text);
        }
        break;

      case 'stage1_start':
      case 'stage2_start':
      case 'stage3_start':
        console.log('[VoiceChat] Stage start:', type);
        if (onStageUpdate) {
          onStageUpdate(type, null);
        }
        break;

      case 'stage1_complete':
        console.log('[VoiceChat] Stage 1 complete');
        if (onStageUpdate) {
          onStageUpdate(type, data, metadata);
        }
        break;

      case 'stage2_complete':
        console.log('[VoiceChat] Stage 2 complete');
        if (onStageUpdate) {
          onStageUpdate(type, data, metadata);
        }
        break;

      case 'stage3_complete':
        console.log('[VoiceChat] Stage 3 complete');
        if (onStageUpdate) {
          onStageUpdate(type, data, metadata);
        }
        break;

      case 'title_complete':
        if (onStageUpdate) {
          onStageUpdate(type, data);
        }
        break;

      case 'audio_start':
        console.log('[VoiceChat] Audio response starting');
        audioChunksRef.current = [];
        break;

      case 'audio_response':
        if (data) {
          audioChunksRef.current.push(base64ToArrayBuffer(data));
        }
        break;

      case 'audio_complete':
        console.log('[VoiceChat] Audio complete, playing response');
        playAudioResponse();
        setIsProcessing(false);
        isProcessingRef.current = false;
        if (onStageUpdate) {
          onStageUpdate('audio_complete', null);
        }
        // Close WebSocket after completion
        if (wsRef.current) {
          wsRef.current.close();
          wsRef.current = null;
        }
        break;

      case 'error':
        console.error('[VoiceChat] Error from server:', message.message);
        setError(message.message || 'Unknown error');
        setIsRecording(false);
        setIsProcessing(false);
        isProcessingRef.current = false;
        break;
    }
  };

  const playAudioResponse = async () => {
    if (audioChunksRef.current.length === 0) {
      console.log('[VoiceChat] No audio to play');
      return;
    }

    try {
      const audioBlob = new Blob(audioChunksRef.current, { type: 'audio/mp3' });
      const audioUrl = URL.createObjectURL(audioBlob);
      const audio = new Audio(audioUrl);

      audio.onended = () => {
        URL.revokeObjectURL(audioUrl);
      };

      await audio.play();
      console.log('[VoiceChat] Audio playback started');
    } catch (e) {
      console.error('[VoiceChat] Audio playback error:', e);
    }
  };

  const base64ToArrayBuffer = (base64) => {
    const binaryString = atob(base64);
    const bytes = new Uint8Array(binaryString.length);
    for (let i = 0; i < binaryString.length; i++) {
      bytes[i] = binaryString.charCodeAt(i);
    }
    return bytes.buffer;
  };

  const startRecording = async () => {
    console.log('[VoiceChat] Starting recording...');
    setError(null);
    setTranscription('');

    try {
      await connectWebSocket();

      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          sampleRate: 24000,
          echoCancellation: true,
          noiseSuppression: true,
        }
      });

      mediaStreamRef.current = stream;
      audioContextRef.current = new AudioContext({ sampleRate: 24000 });
      const source = audioContextRef.current.createMediaStreamSource(stream);
      const processor = audioContextRef.current.createScriptProcessor(4096, 1, 1);
      processorRef.current = processor;

      processor.onaudioprocess = (e) => {
        if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;

        const inputData = e.inputBuffer.getChannelData(0);
        const pcm16 = float32ToPcm16(inputData);
        const base64 = arrayBufferToBase64(pcm16.buffer);

        wsRef.current.send(JSON.stringify({
          type: 'audio',
          data: base64
        }));
      };

      source.connect(processor);
      processor.connect(audioContextRef.current.destination);

      wsRef.current.send(JSON.stringify({ type: 'start_recording' }));
      console.log('[VoiceChat] Recording started');

    } catch (e) {
      console.error('[VoiceChat] Failed to start recording:', e);
      setError(e.message || 'Failed to start recording');
      cleanupAudio();
    }
  };

  const stopRecording = async () => {
    console.log('[VoiceChat] Stopping recording...');

    // Stop audio capture first
    cleanupAudio();

    // Send stop message
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      console.log('[VoiceChat] Sending stop_recording');
      wsRef.current.send(JSON.stringify({ type: 'stop_recording' }));
    }

    setIsRecording(false);
    console.log('[VoiceChat] Recording stopped, waiting for response...');
  };

  const toggleRecording = async () => {
    if (isRecording) {
      await stopRecording();
    } else {
      await startRecording();
    }
  };

  const float32ToPcm16 = (float32Array) => {
    const pcm16 = new Int16Array(float32Array.length);
    for (let i = 0; i < float32Array.length; i++) {
      const s = Math.max(-1, Math.min(1, float32Array[i]));
      pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
    }
    return pcm16;
  };

  const arrayBufferToBase64 = (buffer) => {
    const bytes = new Uint8Array(buffer);
    let binary = '';
    for (let i = 0; i < bytes.byteLength; i++) {
      binary += String.fromCharCode(bytes[i]);
    }
    return btoa(binary);
  };

  return {
    isRecording,
    isProcessing,
    transcription,
    error,
    toggleRecording,
    startRecording,
    stopRecording,
  };
}

/**
 * API client for the KT LLM Council backend.
 */

const API_BASE = '/council';

// Store token in memory
let authToken = null;

/**
 * Make an authenticated fetch request.
 */
async function authFetch(url, options = {}) {
  const headers = {
    'Content-Type': 'application/json',
    ...options.headers,
  };

  if (authToken) {
    headers['Authorization'] = `Bearer ${authToken}`;
  }

  const response = await fetch(url, {
    ...options,
    headers,
  });

  if (response.status === 401) {
    // Clear token and redirect to login
    authToken = null;
    localStorage.removeItem('token');
    window.location.href = '/council/login';
    throw new Error('Unauthorized');
  }

  return response;
}

export const api = {
  /**
   * Set the authentication token.
   */
  setToken(token) {
    authToken = token;
  },

  /**
   * Login with email and password.
   */
  async login(email, password) {
    const response = await fetch(`${API_BASE}/api/auth/login`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ email, password }),
    });

    if (!response.ok) {
      const contentType = response.headers.get('content-type');
      if (contentType && contentType.includes('application/json')) {
        const error = await response.json();
        throw new Error(error.detail || 'Login failed');
      }
      throw new Error('Login failed - server returned an unexpected response');
    }

    return response.json();
  },

  /**
   * Get current user info.
   */
  async getMe() {
    const response = await authFetch(`${API_BASE}/api/auth/me`);

    if (!response.ok) {
      throw new Error('Failed to get user info');
    }

    return response.json();
  },

  /**
   * List all users (admin only).
   */
  async listUsers() {
    const response = await authFetch(`${API_BASE}/api/admin/users`);

    if (!response.ok) {
      throw new Error('Failed to list users');
    }

    return response.json();
  },

  /**
   * Create a new user (admin only).
   */
  async createUser(email, password, name, isAdmin = false) {
    const response = await authFetch(`${API_BASE}/api/admin/users`, {
      method: 'POST',
      body: JSON.stringify({ email, password, name, is_admin: isAdmin }),
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || 'Failed to create user');
    }

    return response.json();
  },

  /**
   * Delete a user (admin only).
   */
  async deleteUser(userId) {
    const response = await authFetch(`${API_BASE}/api/admin/users/${userId}`, {
      method: 'DELETE',
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || 'Failed to delete user');
    }

    return response.json();
  },

  /**
   * List all conversations.
   */
  async listConversations() {
    const response = await authFetch(`${API_BASE}/api/conversations`);
    if (!response.ok) {
      throw new Error('Failed to list conversations');
    }
    return response.json();
  },

  /**
   * Create a new conversation.
   */
  async createConversation() {
    const response = await authFetch(`${API_BASE}/api/conversations`, {
      method: 'POST',
      body: JSON.stringify({}),
    });
    if (!response.ok) {
      throw new Error('Failed to create conversation');
    }
    return response.json();
  },

  /**
   * Get a specific conversation.
   */
  async getConversation(conversationId) {
    const response = await authFetch(
      `${API_BASE}/api/conversations/${conversationId}`
    );
    if (!response.ok) {
      throw new Error('Failed to get conversation');
    }
    return response.json();
  },

  /**
   * Send a message in a conversation.
   */
  async sendMessage(conversationId, content) {
    const response = await authFetch(
      `${API_BASE}/api/conversations/${conversationId}/message`,
      {
        method: 'POST',
        body: JSON.stringify({ content }),
      }
    );
    if (!response.ok) {
      throw new Error('Failed to send message');
    }
    return response.json();
  },

  /**
   * Send a message and receive streaming updates.
   * @param {string} conversationId - The conversation ID
   * @param {string} content - The message content
   * @param {function} onEvent - Callback function for each event: (eventType, data) => void
   * @returns {Promise<void>}
   */
  async sendMessageStream(conversationId, content, onEvent) {
    const headers = {
      'Content-Type': 'application/json',
    };

    if (authToken) {
      headers['Authorization'] = `Bearer ${authToken}`;
    }

    const response = await fetch(
      `${API_BASE}/api/conversations/${conversationId}/message/stream`,
      {
        method: 'POST',
        headers,
        body: JSON.stringify({ content }),
      }
    );

    if (!response.ok) {
      throw new Error('Failed to send message');
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      const chunk = decoder.decode(value);
      const lines = chunk.split('\n');

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const data = line.slice(6);
          try {
            const event = JSON.parse(data);
            onEvent(event.type, event);
          } catch (e) {
            console.error('Failed to parse SSE event:', e);
          }
        }
      }
    }
  },

  /**
   * Get the WebSocket URL for voice chat with auth token.
   */
  getVoiceWebSocketUrl(conversationId) {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsBase = `${protocol}//${window.location.host}`;
    return `${wsBase}/api/conversations/${conversationId}/voice?token=${authToken}`;
  },
};

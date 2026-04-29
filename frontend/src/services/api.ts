import type { AnalyzeResponse, ConversationMessage } from '@/types';

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export async function analyzeQuery(
  query: string,
  conversationHistory?: ConversationMessage[],
): Promise<AnalyzeResponse> {
  const body: Record<string, unknown> = { query };
  if (conversationHistory && conversationHistory.length > 0) {
    body.conversation_history = conversationHistory;
  }

  const response = await fetch(`${API_URL}/analyze`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    const errorBody = await response.text().catch(() => '');
    if (response.status === 504) {
      throw new Error('Analysis timed out. The query may be too complex.');
    }
    if (response.status === 422) {
      throw new Error('Invalid query. Please check your input.');
    }
    throw new Error(errorBody || `Request failed with status ${response.status}`);
  }

  return response.json();
}

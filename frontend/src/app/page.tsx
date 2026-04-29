'use client';

import { Box, ScrollArea, Stack, Title } from '@mantine/core';
import { useState } from 'react';
import { AppHeader } from '@/components/AppHeader';
import { Sidebar } from '@/components/Sidebar';
import { QueryInput } from '@/components/QueryInput';
import { AnalysisResult } from '@/components/AnalysisResult';
import { analyzeQuery } from '@/services/api';
import type { AnalyzeResponse, ConversationMessage } from '@/types';

export default function Home() {
  const [response, setResponse] = useState<AnalyzeResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [conversationHistory, setConversationHistory] = useState<ConversationMessage[]>([]);

  const handleSubmit = async (query: string) => {
    setLoading(true);
    try {
      // For follow-ups, include prior assistant response in history
      let history = conversationHistory;
      if (response) {
        history = [
          ...conversationHistory,
          { role: 'assistant' as const, content: JSON.stringify(response.answer) },
        ];
      }

      const res = await analyzeQuery(query, history.length > 0 ? history : undefined);
      setResponse(res);

      // Accumulate history
      setConversationHistory([
        ...history,
        { role: 'user' as const, content: query },
      ]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <Box style={{ display: 'flex', flexDirection: 'column', height: '100vh' }}>
      <AppHeader />

      <Box style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        <Sidebar />

        <ScrollArea style={{ flex: 1 }} p="lg">
          <Box maw={960} mx="auto">
            <Stack gap="xl">
              <Title
                order={2}
                fw={600}
                style={{ fontFamily: "'IBM Plex Sans', sans-serif", fontSize: 32 }}
              >
                Investigate
              </Title>

              <QueryInput
                onSubmit={handleSubmit}
                loading={loading}
                hasResults={!!response}
              />

              {response && (
                <AnalysisResult
                  data={response.answer}
                  requestId={response.request_id}
                  durationMs={response.duration_ms}
                />
              )}
            </Stack>
          </Box>
        </ScrollArea>
      </Box>
    </Box>
  );
}

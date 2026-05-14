'use client';

import { Box, ScrollArea, Stack, Title } from '@mantine/core';
import { useState } from 'react';
import { AppHeader } from '@/components/AppHeader';
import { Sidebar } from '@/components/Sidebar';
import { QueryInput } from '@/components/QueryInput';
import { AnalysisResult } from '@/components/AnalysisResult';
import { analyzeQuery } from '@/services/api';
import type { AnalyzeResponse, ConversationMessage } from '@/types';

const CONFIRMATION_CODE_REGEX = /\b\d{16}\b/g;

export default function Home() {
  const [response, setResponse] = useState<AnalyzeResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [conversationHistory, setConversationHistory] = useState<ConversationMessage[]>([]);

  const handleSubmit = async (query: string) => {
    setLoading(true);
    try {
      const requestedCodes = query.match(CONFIRMATION_CODE_REGEX) || [];
      const isExplicitSessionQuery = requestedCodes.length > 0;
      const history =
        !isExplicitSessionQuery && conversationHistory.length > 0
          ? conversationHistory
          : undefined;
      const res = await analyzeQuery(query, history);
      setResponse(res);

      // Accumulate ordered turns: user question -> assistant answer
      const nextHistory: ConversationMessage[] = [
        { role: 'user' as const, content: query },
        { role: 'assistant' as const, content: JSON.stringify(res.answer) },
      ];
      setConversationHistory(
        isExplicitSessionQuery
          ? nextHistory
          : [...conversationHistory, ...nextHistory],
      );
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
                AI Support Analyst
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

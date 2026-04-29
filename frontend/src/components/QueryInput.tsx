'use client';

import { Button, Textarea, Alert, Box, Text, Loader } from '@mantine/core';
import { useState, useEffect, useRef } from 'react';

interface QueryInputProps {
  onSubmit: (query: string) => Promise<void>;
  loading: boolean;
  hasResults: boolean;
}

const LOADING_MESSAGES = [
  '🔍 Querying data sources...',
  '📊 Analyzing logs from App Insights...',
  '🗄️ Fetching session data from Cosmos DB...',
  '☸️ Checking infrastructure logs...',
  '🤖 AI is correlating findings...',
];

export function QueryInput({ onSubmit, loading, hasResults }: QueryInputProps) {
  const [query, setQuery] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loadingMsgIndex, setLoadingMsgIndex] = useState(0);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (loading) {
      setLoadingMsgIndex(0);
      intervalRef.current = setInterval(() => {
        setLoadingMsgIndex((prev) => (prev + 1) % LOADING_MESSAGES.length);
      }, 3000);
    } else if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [loading]);

  const handleSubmit = async () => {
    if (!query.trim() || loading) return;
    setError(null);

    try {
      await onSubmit(query.trim());
      setQuery('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'An unexpected error occurred');
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      handleSubmit();
    }
  };

  return (
    <Box>
      <Textarea
        label={hasResults ? 'Ask a follow-up question' : 'Type your query here'}
        placeholder={
          hasResults
            ? 'e.g. Show me the proctor assignment details for this session'
            : 'e.g. What happened with confirmation code 0000000109109210?'
        }
        value={query}
        onChange={(e) => setQuery(e.currentTarget.value)}
        onKeyDown={handleKeyDown}
        disabled={loading}
        minRows={3}
        autosize
        styles={{
          input: {
            borderColor: '#878c96',
            borderWidth: 2,
            borderRadius: 8,
            fontFamily: "'IBM Plex Sans', sans-serif",
          },
          label: {
            fontWeight: 500,
            fontSize: 16,
            marginBottom: 4,
            fontFamily: "'IBM Plex Sans', sans-serif",
          },
        }}
        description={
          hasResults
            ? 'Dig deeper — ask about assignments, chat, errors, or infra'
            : 'Enter a confirmation code or ask a question about session logs'
        }
      />

      <Box mt="sm" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Text size="xs" c="dimmed" style={{ fontFamily: "'IBM Plex Sans', sans-serif" }}>
          ⌘+Enter to submit
        </Text>
        <Button
          onClick={handleSubmit}
          loading={loading}
          color="green"
          radius="md"
          size="md"
          disabled={!query.trim()}
          styles={{
            root: {
              backgroundColor: '#00855f',
              border: '2px solid #00855f',
              fontWeight: 600,
              fontFamily: "'IBM Plex Sans', sans-serif",
              minHeight: 45,
            },
          }}
        >
          {hasResults ? 'Ask Follow-up' : 'Investigate'}
        </Button>
      </Box>

      {loading && (
        <Box
          mt="md"
          p="md"
          style={{
            backgroundColor: '#f0f4f8',
            borderRadius: 8,
            display: 'flex',
            alignItems: 'center',
            gap: 12,
          }}
        >
          <Loader size="sm" color="#00855f" type="dots" />
          <Text size="sm" fw={500} style={{ fontFamily: "'IBM Plex Sans', sans-serif" }}>
            {LOADING_MESSAGES[loadingMsgIndex]}
          </Text>
        </Box>
      )}

      {error && (
        <Alert color="red" mt="sm" title="Error">
          {error}
        </Alert>
      )}
    </Box>
  );
}

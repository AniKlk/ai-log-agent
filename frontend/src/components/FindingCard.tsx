import { Badge, Card, Collapse, Text, UnstyledButton, Group } from '@mantine/core';
import { useState } from 'react';
import type { Finding } from '@/types';

const SEVERITY_COLORS: Record<Finding['severity'], string> = {
  critical: 'red',
  warning: 'yellow',
  info: 'blue',
};

interface FindingCardProps {
  finding: Finding;
}

export function FindingCard({ finding }: FindingCardProps) {
  const [opened, setOpened] = useState(false);

  return (
    <Card shadow="xs" padding="md" radius="md" withBorder>
      <Group justify="space-between" mb="xs">
        <Badge color={SEVERITY_COLORS[finding.severity]} variant="filled">
          {finding.severity}
        </Badge>
      </Group>
      <Text size="sm" mb="xs">
        {finding.description}
      </Text>
      {finding.evidence.length > 0 && (
        <>
          <UnstyledButton onClick={() => setOpened((o) => !o)}>
            <Text size="xs" c="dimmed" td="underline">
              {opened ? 'Hide' : 'Show'} evidence ({finding.evidence.length})
            </Text>
          </UnstyledButton>
          <Collapse expanded={opened}>
            <Card mt="xs" bg="gray.0" padding="xs" radius="sm">
              {finding.evidence.map((e, i) => (
                <Text key={i} size="xs" ff="monospace" mb={4}>
                  {e}
                </Text>
              ))}
            </Card>
          </Collapse>
        </>
      )}
    </Card>
  );
}

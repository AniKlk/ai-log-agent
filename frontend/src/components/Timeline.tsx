import { Timeline as MantineTimeline, Text } from '@mantine/core';
import type { TimelineEntry } from '@/types';

const SEVERITY_COLORS: Record<string, string> = {
  critical: 'red',
  warning: 'yellow',
  info: 'blue',
};

interface TimelineProps {
  entries: TimelineEntry[];
}

export function Timeline({ entries }: TimelineProps) {
  if (entries.length === 0) {
    return (
      <Text size="sm" c="dimmed">
        No timeline events available.
      </Text>
    );
  }

  return (
    <MantineTimeline active={entries.length - 1} bulletSize={16} lineWidth={2}>
      {entries.map((entry, index) => (
        <MantineTimeline.Item
          key={index}
          color={entry.severity ? SEVERITY_COLORS[entry.severity] || 'gray' : 'gray'}
          title={entry.event}
        >
          {entry.timestamp && (
            <Text size="xs" c="dimmed" ff="monospace">
              {entry.timestamp}
            </Text>
          )}
        </MantineTimeline.Item>
      ))}
    </MantineTimeline>
  );
}

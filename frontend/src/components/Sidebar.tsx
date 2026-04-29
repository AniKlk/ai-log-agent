'use client';

import { Box, Text } from '@mantine/core';

interface SidebarProps {
  activeItem?: string;
}

const NAV_ITEMS = [
  { label: 'Investigate', key: 'investigate' },
];

export function Sidebar({ activeItem = 'investigate' }: SidebarProps) {
  return (
    <Box
      component="nav"
      style={{
        width: 320,
        minWidth: 320,
        backgroundColor: '#071c35',
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
        padding: '16px 12px',
        overflowY: 'auto',
        height: '100%',
      }}
    >
      {NAV_ITEMS.map((item) => (
        <Box
          key={item.key}
          style={{
            backgroundColor:
              item.key === activeItem ? '#ffcb33' : 'transparent',
            borderRadius: 8,
            padding: '8px 12px',
            height: 48,
            display: 'flex',
            alignItems: 'center',
            cursor: 'pointer',
          }}
        >
          <Text
            fw={600}
            size="lg"
            c={item.key === activeItem ? '#071c35' : '#f7f8fa'}
            style={{
              fontFamily: "'IBM Plex Sans', sans-serif",
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {item.label}
          </Text>
        </Box>
      ))}
    </Box>
  );
}

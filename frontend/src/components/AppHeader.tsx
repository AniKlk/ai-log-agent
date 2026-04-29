'use client';

import { Group, Text, Box } from '@mantine/core';
import Image from 'next/image';

export function AppHeader() {
  return (
    <Box
      component="header"
      style={{
        height: 72,
        backgroundColor: '#fff',
        boxShadow: '0px 0px 4px rgba(7,28,53,0.05), 0px 4px 4px rgba(7,28,53,0.1)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '0 32px',
        zIndex: 100,
        position: 'relative',
      }}
    >
      <Group gap={16}>
        <Image
          src="/prometric-logo.png"
          alt="Prometric"
          width={159}
          height={49}
          priority
          style={{ objectFit: 'contain' }}
        />
        <Text
          fw={600}
          size="lg"
          style={{ fontFamily: "'IBM Plex Sans', sans-serif" }}
        >
          ProProctor Investigator
        </Text>
      </Group>
    </Box>
  );
}

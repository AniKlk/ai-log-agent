'use client';

import { MantineProvider, createTheme } from '@mantine/core';
import { Notifications } from '@mantine/notifications';
import '@mantine/core/styles.css';
import '@mantine/notifications/styles.css';

const theme = createTheme({
  primaryColor: 'green',
  colors: {
    green: [
      '#e6f7f1',
      '#b3e6d4',
      '#80d5b7',
      '#4dc49a',
      '#1ab37d',
      '#00855f',
      '#006b4c',
      '#005139',
      '#003726',
      '#001d13',
    ],
  },
  fontFamily: "'IBM Plex Sans', sans-serif",
  headings: {
    fontFamily: "'IBM Plex Sans', sans-serif",
    fontWeight: '600',
  },
  radius: {
    xs: '4px',
    sm: '6px',
    md: '8px',
    lg: '12px',
    xl: '16px',
  },
});

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <MantineProvider theme={theme}>
      <Notifications position="top-right" />
      {children}
    </MantineProvider>
  );
}

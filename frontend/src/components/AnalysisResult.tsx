import {
  Anchor,
  Alert,
  Badge,
  Box,
  Button,
  Card,
  Divider,
  Group,
  ScrollArea,
  Stack,
  Table,
  Text,
  Title,
} from '@mantine/core';
import jsPDF from 'jspdf';
import autoTable from 'jspdf-autotable';
import * as XLSX from 'xlsx';
import { saveAs } from 'file-saver';
import type { AgentOutput, TimelineEntry } from '@/types';
import { FindingCard } from './FindingCard';
import { Timeline } from './Timeline';

interface AnalysisResultProps {
  data: AgentOutput;
  requestId: string;
  durationMs: number;
}

const SUMMARY_LABELS: Array<{ key: keyof NonNullable<AgentOutput['source_summary']>; label: string }> = [
  { key: 'app_insights_events', label: 'App Insights Events' },
  { key: 'infra_events', label: 'Infrastructure Events' },
  { key: 'cosmos_session_records', label: 'Cosmos Session Records' },
  { key: 'cosmos_session_log_records', label: 'Cosmos Session Log Records' },
  { key: 'cosmos_conference_records', label: 'Cosmos Conference Records' },
  { key: 'cosmos_assignment_records', label: 'Cosmos Assignment Records' },
];

const LIFECYCLE_DETAIL_PATTERNS = [
  /app-insights\s+exit\s+marker\s+rollup/i,
  /app-insights\s+login\s+marker\s+rollup/i,
  /lifecycle\s+correlation\s+red\s+flag/i,
  /candidate-app\s+exit\s+marker/i,
  /missing\s+in\s+cosmos/i,
];

const ISO_TIMESTAMP_REGEX = /\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z/;
const CONFIRMATION_CODE_REGEX = /\b\d{16}\b/g;

function extractIsoTimestamp(text: string): string | null {
  const match = text.match(ISO_TIMESTAMP_REGEX);
  return match?.[0] ?? null;
}

function inferSeverity(eventText: string): 'critical' | 'warning' | 'info' {
  const normalized = eventText.toLowerCase();
  if (normalized.includes('red flag') || normalized.includes('missing in cosmos')) {
    return 'critical';
  }
  if (normalized.includes('warning') || normalized.includes('gap')) {
    return 'warning';
  }
  return 'info';
}

function collectConfirmationCodes(data: AgentOutput): string[] {
  const codes = new Set<string>();

  for (const code of data.confirmation_codes || []) {
    codes.add(code);
  }

  Object.keys(data.per_confirmation_code_summaries || {}).forEach((code) => codes.add(code));
  Object.keys(data.per_confirmation_code_source_summary || {}).forEach((code) => codes.add(code));

  const candidateTexts: string[] = [
    data.summary,
    data.root_cause || '',
    ...data.timeline.map((entry) => entry.event),
    ...data.key_findings.map((finding) => finding.description),
    ...data.key_findings.flatMap((finding) => finding.evidence),
    ...(data.warnings || []),
  ];

  for (const text of candidateTexts) {
    const matches = text.match(CONFIRMATION_CODE_REGEX);
    if (!matches) {
      continue;
    }
    for (const match of matches) {
      codes.add(match);
    }
  }

  return Array.from(codes).sort();
}

export function AnalysisResult({
  data,
  requestId,
  durationMs,
}: AnalysisResultProps) {
  const hasSourceSummary = !!data.source_summary;
  const perCodeSummaries = Object.entries(data.per_confirmation_code_source_summary || {});
  const perCodeExecutiveSummaries = Object.entries(data.per_confirmation_code_summaries || {});
  const confirmationCodes = collectConfirmationCodes(data);

  const synthesizedLifecycleDetails = new Set<string>();
  for (const warning of data.warnings || []) {
    if (warning && LIFECYCLE_DETAIL_PATTERNS.some((pattern) => pattern.test(warning))) {
      synthesizedLifecycleDetails.add(warning.trim());
    }
  }
  for (const finding of data.key_findings) {
    if (LIFECYCLE_DETAIL_PATTERNS.some((pattern) => pattern.test(finding.description))) {
      synthesizedLifecycleDetails.add(finding.description.trim());
    }
    for (const evidence of finding.evidence) {
      if (LIFECYCLE_DETAIL_PATTERNS.some((pattern) => pattern.test(evidence))) {
        synthesizedLifecycleDetails.add(evidence.trim());
      }
    }
  }

  const existingTimelineEvents = new Set(data.timeline.map((entry) => entry.event.trim()));
  const derivedLifecycleEntries: TimelineEntry[] = [];
  for (const detail of synthesizedLifecycleDetails) {
    if (existingTimelineEvents.has(detail)) {
      continue;
    }

    derivedLifecycleEntries.push({
      timestamp: extractIsoTimestamp(detail) ?? null,
      event: detail,
      severity: inferSeverity(detail),
    });
  }

  const activityLogEntries = [...data.timeline, ...derivedLifecycleEntries].sort((a, b) => {
    const ta = a.timestamp ?? '';
    const tb = b.timestamp ?? '';
    if (!ta && !tb) return 0;
    if (!ta) return 1;  // nulls last
    if (!tb) return -1;
    return ta.localeCompare(tb);
  });

  const exportToPdf = () => {
    const doc = new jsPDF();
    const pageWidth = doc.internal.pageSize.getWidth();
    let y = 20;

    doc.setFontSize(18);
    doc.text('ProProctor Investigation Report', 14, y);
    y += 12;

    // Executive Summary
    doc.setFontSize(12);
    doc.setFont('helvetica', 'bold');
    doc.text('Executive Summary', 14, y);
    y += 7;
    doc.setFont('helvetica', 'normal');
    doc.setFontSize(10);
    const summaryLines = doc.splitTextToSize(data.summary, pageWidth - 28);
    doc.text(summaryLines, 14, y);
    y += summaryLines.length * 5 + 6;

    // Root Cause
    if (data.root_cause) {
      doc.setFontSize(12);
      doc.setFont('helvetica', 'bold');
      doc.text(`Root Cause (${data.root_cause_confidence || 'unknown'})`, 14, y);
      y += 7;
      doc.setFont('helvetica', 'normal');
      doc.setFontSize(10);
      const rcLines = doc.splitTextToSize(data.root_cause, pageWidth - 28);
      doc.text(rcLines, 14, y);
      y += rcLines.length * 5 + 6;
    }

    // Confirmation codes
    if (confirmationCodes.length > 0) {
      if (y > 240) { doc.addPage(); y = 20; }
      doc.setFontSize(12);
      doc.setFont('helvetica', 'bold');
      doc.text('Confirmation Codes', 14, y);
      y += 7;
      doc.setFont('helvetica', 'normal');
      doc.setFontSize(10);
      const codeLines = doc.splitTextToSize(confirmationCodes.join(', '), pageWidth - 28);
      doc.text(codeLines, 14, y);
      y += codeLines.length * 5 + 6;
    }

    // Per-confirmation-code executive summaries
    if (perCodeExecutiveSummaries.length > 0) {
      if (y > 240) { doc.addPage(); y = 20; }
      doc.setFontSize(12);
      doc.setFont('helvetica', 'bold');
      doc.text('Per Confirmation Code Summaries', 14, y);
      y += 7;

      doc.setFont('helvetica', 'normal');
      doc.setFontSize(10);
      for (const [confirmationCode, codeSummary] of perCodeExecutiveSummaries) {
        const lines = doc.splitTextToSize(`${confirmationCode}: ${codeSummary}`, pageWidth - 28);
        if (y + lines.length * 5 > 270) {
          doc.addPage();
          y = 20;
        }
        doc.text(lines, 14, y);
        y += lines.length * 5 + 4;
      }
    }

    // Key Findings table
    if (data.key_findings.length > 0) {
      doc.setFontSize(12);
      doc.setFont('helvetica', 'bold');
      doc.text('Key Findings', 14, y);
      y += 4;

      autoTable(doc, {
        startY: y,
        head: [['Severity', 'Description', 'Evidence']],
        body: data.key_findings.map((f) => [
          f.severity,
          f.description,
          f.evidence.join('; '),
        ]),
        styles: { fontSize: 8 },
        headStyles: { fillColor: [0, 133, 95] },
      });
      y = (doc as unknown as { lastAutoTable: { finalY: number } }).lastAutoTable.finalY + 8;
    }

    // Timeline table
    if (activityLogEntries.length > 0) {
      if (y > 240) { doc.addPage(); y = 20; }
      doc.setFontSize(12);
      doc.setFont('helvetica', 'bold');
      doc.text('Activity Timeline', 14, y);
      y += 4;

      autoTable(doc, {
        startY: y,
        head: [['Timestamp', 'Event', 'Severity']],
        body: activityLogEntries.map((t) => [t.timestamp, t.event, t.severity || '']),
        styles: { fontSize: 8 },
        headStyles: { fillColor: [0, 133, 95] },
      });
      y = (doc as unknown as { lastAutoTable: { finalY: number } }).lastAutoTable.finalY + 8;
    }

    // Source summary tables
    if (hasSourceSummary && data.source_summary) {
      if (y > 240) { doc.addPage(); y = 20; }
      doc.setFontSize(12);
      doc.setFont('helvetica', 'bold');
      doc.text('Source Summary (Aggregate)', 14, y);
      y += 4;

      autoTable(doc, {
        startY: y,
        head: [['Source', 'Count']],
        body: SUMMARY_LABELS.map(({ key, label }) => [label, String(data.source_summary?.[key] ?? 0)]),
        styles: { fontSize: 8 },
        headStyles: { fillColor: [0, 133, 95] },
      });
      y = (doc as unknown as { lastAutoTable: { finalY: number } }).lastAutoTable.finalY + 8;
    }

    if (perCodeSummaries.length > 0) {
      for (const [confirmationCode, summary] of perCodeSummaries) {
        if (y > 240) { doc.addPage(); y = 20; }
        doc.setFontSize(12);
        doc.setFont('helvetica', 'bold');
        doc.text(`Source Summary (${confirmationCode})`, 14, y);
        y += 4;

        autoTable(doc, {
          startY: y,
          head: [['Source', 'Count']],
          body: SUMMARY_LABELS.map(({ key, label }) => [label, String(summary[key] ?? 0)]),
          styles: { fontSize: 8 },
          headStyles: { fillColor: [0, 133, 95] },
        });
        y = (doc as unknown as { lastAutoTable: { finalY: number } }).lastAutoTable.finalY + 8;
      }
    }

    // Footer
    const finalPage = doc.getNumberOfPages();
    for (let i = 1; i <= finalPage; i++) {
      doc.setPage(i);
      doc.setFontSize(8);
      doc.setTextColor(150);
      doc.text(`Request: ${requestId} | Duration: ${durationMs}ms`, 14, doc.internal.pageSize.getHeight() - 10);
    }

    doc.save(`investigation-${requestId}.pdf`);
  };

  const exportToExcel = () => {
    const wb = XLSX.utils.book_new();

    // Summary sheet
    const summaryData = [
      ['Executive Summary', data.summary],
      ['Root Cause', data.root_cause || 'N/A'],
      ['Confidence', data.root_cause_confidence || 'N/A'],
      ['Confirmation Codes', confirmationCodes.length > 0 ? confirmationCodes.join(', ') : 'N/A'],
      ['Request ID', requestId],
      ['Duration (ms)', String(durationMs)],
      ['Tools Used', data.tools_invoked.join(', ')],
    ];
    const summarySheet = XLSX.utils.aoa_to_sheet(summaryData);
    summarySheet['!cols'] = [{ wch: 20 }, { wch: 80 }];
    XLSX.utils.book_append_sheet(wb, summarySheet, 'Summary');

    if (perCodeExecutiveSummaries.length > 0) {
      const perCodeSummaryData: (string | number)[][] = [['Confirmation Code', 'Executive Summary']];
      for (const [confirmationCode, codeSummary] of perCodeExecutiveSummaries) {
        perCodeSummaryData.push([confirmationCode, codeSummary]);
      }
      const perCodeSummarySheet = XLSX.utils.aoa_to_sheet(perCodeSummaryData);
      perCodeSummarySheet['!cols'] = [{ wch: 24 }, { wch: 100 }];
      XLSX.utils.book_append_sheet(wb, perCodeSummarySheet, 'Per Code Narrative');
    }

    if (confirmationCodes.length > 0) {
      const confirmationCodeData: (string | number)[][] = [['Confirmation Code']];
      for (const confirmationCode of confirmationCodes) {
        confirmationCodeData.push([confirmationCode]);
      }
      const confirmationCodeSheet = XLSX.utils.aoa_to_sheet(confirmationCodeData);
      confirmationCodeSheet['!cols'] = [{ wch: 24 }];
      XLSX.utils.book_append_sheet(wb, confirmationCodeSheet, 'Confirmation Codes');
    }

    // Aggregate source summary sheet
    if (hasSourceSummary && data.source_summary) {
      const aggregateSummaryData = [
        ['Source', 'Count'],
        ...SUMMARY_LABELS.map(({ key, label }) => [label, data.source_summary?.[key] ?? 0]),
      ];
      const aggregateSheet = XLSX.utils.aoa_to_sheet(aggregateSummaryData);
      aggregateSheet['!cols'] = [{ wch: 36 }, { wch: 16 }];
      XLSX.utils.book_append_sheet(wb, aggregateSheet, 'Source Summary');
    }

    // Per confirmation code source summary sheet
    if (perCodeSummaries.length > 0) {
      const perCodeData: (string | number)[][] = [['Confirmation Code', 'Source', 'Count']];
      for (const [confirmationCode, summary] of perCodeSummaries) {
        for (const { key, label } of SUMMARY_LABELS) {
          perCodeData.push([confirmationCode, label, summary[key] ?? 0]);
        }
      }
      const perCodeSheet = XLSX.utils.aoa_to_sheet(perCodeData);
      perCodeSheet['!cols'] = [{ wch: 24 }, { wch: 36 }, { wch: 16 }];
      XLSX.utils.book_append_sheet(wb, perCodeSheet, 'Per Code Summary');
    }

    // Findings sheet
    if (data.key_findings.length > 0) {
      const findingsData = [
        ['Severity', 'Description', 'Evidence'],
        ...data.key_findings.map((f) => [f.severity, f.description, f.evidence.join('; ')]),
      ];
      const findingsSheet = XLSX.utils.aoa_to_sheet(findingsData);
      findingsSheet['!cols'] = [{ wch: 12 }, { wch: 50 }, { wch: 60 }];
      XLSX.utils.book_append_sheet(wb, findingsSheet, 'Findings');
    }

    // Timeline sheet
    if (activityLogEntries.length > 0) {
      const timelineData = [
        ['Timestamp', 'Event', 'Severity'],
        ...activityLogEntries.map((t) => [t.timestamp, t.event, t.severity || '']),
      ];
      const timelineSheet = XLSX.utils.aoa_to_sheet(timelineData);
      timelineSheet['!cols'] = [{ wch: 25 }, { wch: 60 }, { wch: 12 }];
      XLSX.utils.book_append_sheet(wb, timelineSheet, 'Timeline');
    }

    const wbOut = XLSX.write(wb, { bookType: 'xlsx', type: 'array' });
    saveAs(new Blob([wbOut], { type: 'application/octet-stream' }), `investigation-${requestId}.xlsx`);
  };

  const handleDownloadLink = (link: string, event: React.MouseEvent<HTMLAnchorElement>) => {
    const normalized = link.trim().toLowerCase();
    if (normalized.includes('export://') && normalized.includes('pdf')) {
      event.preventDefault();
      exportToPdf();
      return;
    }
    if (
      normalized.includes('export://') &&
      (normalized.includes('xlsx') || normalized.includes('xls') || normalized.includes('excel'))
    ) {
      event.preventDefault();
      exportToExcel();
    }
  };

  return (
    <Stack gap="xl">
      {/* Results section — side-by-side Activity Log + Findings */}
      <Box>
        <Box
          px="md"
          py="xs"
          mb="sm"
        >
          <Title
            order={3}
            fw={600}
            style={{ fontFamily: "'IBM Plex Sans', sans-serif", fontSize: 25.6 }}
          >
            Results
          </Title>
        </Box>

        <Group align="flex-start" grow gap="md" px="md">
          {/* Activity Log (Timeline) */}
          <Card
            withBorder
            radius="md"
            padding="md"
            style={{ flex: 1, minWidth: 0 }}
          >
            <Title order={4} fw={600} mb="sm">
              Activity Log
            </Title>
            <ScrollArea h={500} offsetScrollbars>
              <Timeline entries={activityLogEntries} />
            </ScrollArea>
          </Card>

          {/* Findings / Source Summary */}
          <Card
            withBorder
            radius="md"
            padding="md"
            style={{ flex: 1, minWidth: 0 }}
          >
            <Title order={4} fw={600} mb="sm">
              Findings
            </Title>
            <ScrollArea h={500} offsetScrollbars>
              <Table verticalSpacing="xs" horizontalSpacing="md">
                <Table.Tbody>
                  {data.key_findings.map((finding, i) => (
                    <Table.Tr key={i}>
                      <Table.Td>
                        <Text size="sm">{finding.description}</Text>
                      </Table.Td>
                      <Table.Td w={110}>
                        <Badge
                          color={
                            finding.severity === 'critical'
                              ? 'red'
                              : finding.severity === 'warning'
                                ? 'yellow'
                                : 'blue'
                          }
                          variant="filled"
                          size="sm"
                          fullWidth
                        >
                          {finding.severity}
                        </Badge>
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </ScrollArea>
          </Card>
        </Group>
      </Box>

      {/* Summary Report section */}
      <Box>
        <Box px="md" py="xs" mb="sm">
          <Title
            order={3}
            fw={600}
            style={{ fontFamily: "'IBM Plex Sans', sans-serif", fontSize: 25.6 }}
          >
            Summary Report
          </Title>
        </Box>

        <Box px="md">
          <Card withBorder radius="md" padding="lg">
            <ScrollArea h={400} offsetScrollbars>
              {/* Executive Summary */}
              <Text fw={600} mb="xs">Executive Summary</Text>
              {data.summary.split('\n').map((line, i) => (
                <Text key={i} size="sm" mb={4}>
                  {line}
                </Text>
              ))}

              {/* Root Cause */}
              {data.root_cause && (
                <>
                  <Divider my="sm" />
                  <Group mb="xs" gap="xs">
                    <Text fw={600}>Root Cause</Text>
                    {data.root_cause_confidence && (
                      <Badge
                        color={
                          data.root_cause_confidence === 'confirmed'
                            ? 'green'
                            : data.root_cause_confidence === 'probable'
                              ? 'yellow'
                              : 'gray'
                        }
                        variant="filled"
                        size="sm"
                      >
                        {data.root_cause_confidence}
                      </Badge>
                    )}
                  </Group>
                  <Text size="sm">{data.root_cause}</Text>
                </>
              )}

              {/* Per-confirmation-code executive summaries */}
              {perCodeExecutiveSummaries.length > 0 && (
                <>
                  <Divider my="sm" />
                  <Text fw={600} mb="xs">Per Confirmation Code Executive Summaries</Text>
                  <Stack gap="sm">
                    {perCodeExecutiveSummaries.map(([confirmationCode, codeSummary]) => (
                      <Card key={confirmationCode} withBorder radius="sm" p="sm">
                        <Text fw={600} size="sm" mb="xs">{confirmationCode}</Text>
                        {codeSummary.split('\n').map((line, i) => (
                          <Text key={`${confirmationCode}-${i}`} size="sm" mb={4}>
                            {line}
                          </Text>
                        ))}
                      </Card>
                    ))}
                  </Stack>
                </>
              )}

              {/* LLM-provided download links */}
              {data.download_links && Object.keys(data.download_links).length > 0 && (
                <>
                  <Divider my="sm" />
                  <Text fw={600} mb="xs">Download Links</Text>
                  <Stack gap="xs">
                    {Object.entries(data.download_links).map(([label, link]) => (
                      <Anchor
                        key={label}
                        href={link}
                        target="_blank"
                        rel="noreferrer"
                        size="sm"
                        onClick={(event) => handleDownloadLink(link, event)}
                      >
                        {label}
                      </Anchor>
                    ))}
                  </Stack>
                </>
              )}

              {/* Detailed Findings */}
              {data.key_findings.length > 0 && (
                <>
                  <Divider my="sm" />
                  <Text fw={600} mb="xs">Key Findings</Text>
                  <Stack gap="sm">
                    {data.key_findings.map((finding, index) => (
                      <FindingCard key={index} finding={finding} />
                    ))}
                  </Stack>
                </>
              )}

              {/* Source Summaries */}
              {(hasSourceSummary || perCodeSummaries.length > 0) && (
                <>
                  <Divider my="sm" />
                  <Text fw={600} mb="xs">Source Summary</Text>

                  {hasSourceSummary && data.source_summary && (
                    <>
                      <Text size="sm" fw={500} mb="xs">Aggregate</Text>
                      <Table withTableBorder withColumnBorders mb="sm">
                        <Table.Thead>
                          <Table.Tr>
                            <Table.Th>Source</Table.Th>
                            <Table.Th w={140}>Count</Table.Th>
                          </Table.Tr>
                        </Table.Thead>
                        <Table.Tbody>
                          {SUMMARY_LABELS.map(({ key, label }) => (
                            <Table.Tr key={key}>
                              <Table.Td>
                                <Text size="sm">{label}</Text>
                              </Table.Td>
                              <Table.Td>
                                <Text size="sm">{data.source_summary?.[key] ?? 0}</Text>
                              </Table.Td>
                            </Table.Tr>
                          ))}
                        </Table.Tbody>
                      </Table>
                    </>
                  )}

                  {perCodeSummaries.length > 0 && (
                    <>
                      <Text size="sm" fw={500} mb="xs">Per Confirmation Code</Text>
                      {perCodeSummaries.map(([confirmationCode, summary]) => (
                        <Card key={confirmationCode} withBorder radius="sm" p="sm" mb="sm">
                          <Text fw={600} size="sm" mb="xs">{confirmationCode}</Text>
                          <Table withTableBorder withColumnBorders>
                            <Table.Thead>
                              <Table.Tr>
                                <Table.Th>Source</Table.Th>
                                <Table.Th w={140}>Count</Table.Th>
                              </Table.Tr>
                            </Table.Thead>
                            <Table.Tbody>
                              {SUMMARY_LABELS.map(({ key, label }) => (
                                <Table.Tr key={`${confirmationCode}-${key}`}>
                                  <Table.Td>
                                    <Text size="sm">{label}</Text>
                                  </Table.Td>
                                  <Table.Td>
                                    <Text size="sm">{summary[key] ?? 0}</Text>
                                  </Table.Td>
                                </Table.Tr>
                              ))}
                            </Table.Tbody>
                          </Table>
                        </Card>
                      ))}
                    </>
                  )}
                </>
              )}
            </ScrollArea>
          </Card>
        </Box>
      </Box>

      {/* Warnings */}
      {data.warnings && data.warnings.length > 0 && (
        <Box px="md">
          <Alert color="yellow" title="Warnings">
            {data.warnings.map((w, i) => (
              <Text key={i} size="sm">
                {w}
              </Text>
            ))}
          </Alert>
        </Box>
      )}

      {/* Metadata footer */}
      <Box px="md">
        <Divider mb="xs" />
        <Group gap="lg" justify="space-between">
          <Group gap="lg">
            <Text size="xs" c="dimmed">
              Request ID: {requestId}
            </Text>
            <Text size="xs" c="dimmed">
              Duration: {durationMs}ms
            </Text>
            {data.tools_invoked.length > 0 && (
              <Text size="xs" c="dimmed">
                Tools: {data.tools_invoked.join(', ')}
              </Text>
            )}
          </Group>
          <Group gap="xs">
            <Button
              variant="outline"
              size="xs"
              color="green"
              onClick={exportToPdf}
              styles={{ root: { fontFamily: "'IBM Plex Sans', sans-serif" } }}
            >
              Export PDF
            </Button>
            <Button
              variant="outline"
              size="xs"
              color="green"
              onClick={exportToExcel}
              styles={{ root: { fontFamily: "'IBM Plex Sans', sans-serif" } }}
            >
              Export Excel
            </Button>
          </Group>
        </Group>
      </Box>
    </Stack>
  );
}

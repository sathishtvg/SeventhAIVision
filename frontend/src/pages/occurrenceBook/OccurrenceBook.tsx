/**
 * The occurrence book: the book itself, the instructions in force, and what
 * each shift hands the next.
 *
 * Entries are still written where they always were — Guard Ops, and the phone.
 * This screen is for reading the book back: searching it, reviewing it,
 * putting an entry right by a further entry, and the summary of a shift.
 */
import { useState } from 'react'
import { Alert, Box, Skeleton, Tab, Tabs } from '@mui/material'
import { useQuery } from '@tanstack/react-query'
import { PageHeader } from '@/components/common/PageHeader'
import { apiError, getKinds } from '@/api/occurrenceBook'
import BookEntries from './BookEntries'
import BookInstructions from './BookInstructions'
import BookSummaries from './BookSummaries'

type Part = 'book' | 'instructions' | 'summaries'

export default function OccurrenceBook() {
  const [part, setPart] = useState<Part>('book')
  const { data: kinds, isLoading, error } = useQuery({ queryKey: ['dob-kinds'], queryFn: getKinds })
  return (
    <Box sx={{ p: 3 }}>
      <PageHeader title="Occurrence Book"
                  subtitle="The book searched, reviewed and corrected; the instructions in force; and what each shift hands the next" />
      {!!error && <Alert severity="error">{apiError(error)}</Alert>}
      {isLoading && <Skeleton height={200} />}
      {kinds && (
        <>
          <Tabs value={part} onChange={(_, v: Part) => setPart(v)} sx={{ mb: 2 }}>
            <Tab value="book" label="The book" />
            {/* Instructions and summaries are handover material: for whoever reads handovers. */}
            {kinds.can_read_handovers && <Tab value="instructions" label="Instructions in force" />}
            {kinds.can_read_handovers && <Tab value="summaries" label="Shift summaries" />}
          </Tabs>
          {part === 'book' && <BookEntries kinds={kinds} />}
          {part === 'instructions' && kinds.can_read_handovers && <BookInstructions />}
          {part === 'summaries' && kinds.can_read_handovers && <BookSummaries canManage={kinds.can_manage_handovers} />}
        </>
      )}
    </Box>
  )
}

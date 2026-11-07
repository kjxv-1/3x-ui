import { z } from 'zod';

export const SortableInboundsSchema = z
  .array(
    z.object({
      id: z.number().int().positive(),
      remark: z.string(),
      subSortIndex: z.number().int(),
    }),
  )
  .refine((rows) => new Set(rows.map((row) => row.id)).size === rows.length);

export type SortableInbound = z.infer<typeof SortableInboundsSchema>[number];
export type SortDirection = 'asc' | 'desc';

export function buildSubscriptionSort(rows: SortableInbound[], direction: SortDirection) {
  const collator = new Intl.Collator('zh-CN', { numeric: true, sensitivity: 'base' });
  return [...rows]
    .sort((a, b) => {
      const comparison = collator.compare(a.remark, b.remark) || a.id - b.id;
      return direction === 'desc' ? -comparison : comparison;
    })
    .map((row, index) => ({ ...row, nextIndex: index + 1 }));
}

// Ignore traffic counters and API row order; changes to labels, membership or
// subscription order require a new preview before any metadata is written.
export function subscriptionSortSnapshot(rows: SortableInbound[]) {
  return JSON.stringify([...rows].sort((a, b) => a.id - b.id));
}

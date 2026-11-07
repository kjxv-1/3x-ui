import { describe, expect, it } from 'vitest';

import {
  buildSubscriptionSort,
  SortableInboundsSchema,
  subscriptionSortSnapshot,
} from '@/pages/inbounds/list/subscriptionSort';

describe('subscription order by remark', () => {
  const rows = [
    { id: 8, remark: 'Node-10', subSortIndex: 1 },
    { id: 3, remark: 'Node-2', subSortIndex: 1 },
    { id: 5, remark: 'node-2', subSortIndex: -1 },
  ];

  it('uses natural numeric ordering with deterministic ties and exact reverse order', () => {
    expect(buildSubscriptionSort(rows, 'asc').map((row) => [row.id, row.nextIndex])).toEqual([
      [3, 1],
      [5, 2],
      [8, 3],
    ]);
    expect(buildSubscriptionSort(rows, 'desc').map((row) => [row.id, row.nextIndex])).toEqual([
      [8, 1],
      [5, 2],
      [3, 3],
    ]);
    expect(rows.map((row) => row.id)).toEqual([8, 3, 5]);
  });

  it('rejects invalid API rows and detects metadata changes without treating traffic as edits', () => {
    expect(SortableInboundsSchema.safeParse([...rows, rows[0]]).success).toBe(false);
    expect(SortableInboundsSchema.safeParse([{ id: 1, remark: 'x' }]).success).toBe(false);
    const fresh = SortableInboundsSchema.parse(
      [...rows].reverse().map((row) => ({ ...row, up: 99 })),
    );
    expect(subscriptionSortSnapshot(fresh)).toBe(subscriptionSortSnapshot(rows));
    expect(subscriptionSortSnapshot(rows.slice(1))).not.toBe(subscriptionSortSnapshot(rows));
    expect(subscriptionSortSnapshot(rows.map((row) => ({ ...row, subSortIndex: 2 })))).not.toBe(
      subscriptionSortSnapshot(rows),
    );
  });
});

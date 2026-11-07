import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';

import { HttpUtil, Msg } from '@/utils';
import { getMessage } from '@/utils/messageBus';
import InboundSortModal from '@/pages/inbounds/list/InboundSortModal';
import InboundList from '@/pages/inbounds/list/InboundList';
import { makeTestQueryClient, renderWithProviders } from './test-utils';

const rows = [
  { id: 10, remark: 'Node-10', subSortIndex: 1, settings: 'do-not-submit', nodeId: 7 },
  { id: 2, remark: 'Node-2', subSortIndex: 1, settings: 'do-not-submit' },
];

function previewNames() {
  return screen
    .getAllByRole('row')
    .slice(1)
    .map((row) => within(row).getAllByRole('cell')[1].textContent);
}

describe('subscription order preview and save', () => {
  beforeEach(() => {
    vi.mocked(HttpUtil.get)
      .mockReset()
      .mockResolvedValue(new Msg(true, '', rows));
    vi.mocked(HttpUtil.post).mockReset().mockResolvedValue(new Msg(true));
    vi.spyOn(getMessage(), 'success').mockImplementation(() => () => {});
  });

  it('switches direction and cancels without writing anything', async () => {
    const close = vi.fn();
    renderWithProviders(<InboundSortModal onClose={close} />);
    await screen.findByText('Node-2');
    expect(previewNames()).toEqual(['Node-2', 'Node-10']);
    fireEvent.click(screen.getByRole('radio', { name: 'Descending' }));
    expect(previewNames()).toEqual(['Node-10', 'Node-2']);
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(close).toHaveBeenCalledOnce();
    expect(HttpUtil.post).not.toHaveBeenCalled();
  });

  it.each([false, true])(
    'opens all inbounds from a filtered list (mobile: %s)',
    async (isMobile) => {
      renderWithProviders(
        <MemoryRouter>
          <InboundList
            dbInbounds={rows.map((row) => ({
              ...row,
              enable: true,
              port: 443,
              protocol: 'vless',
              up: 0,
              down: 0,
              total: 0,
              expiryTime: 0,
              _expiryTime: null,
              settings: '{}',
              streamSettings: '{}',
            }))}
            clientCount={{}}
            onlineClients={[]}
            lastOnlineMap={{}}
            inboundSpeed={{}}
            expireDiff={0}
            trafficDiff={0}
            pageSize={20}
            isMobile={isMobile}
            subEnable
            nodesById={new Map()}
            hasActiveNode={false}
            hosts={[]}
            onAddInbound={vi.fn()}
            onGeneralAction={vi.fn()}
            onRowAction={vi.fn()}
            onBulkDelete={async () => true}
          />
        </MemoryRouter>,
      );
      fireEvent.change(screen.getByRole('textbox', { name: 'Search' }), {
        target: { value: 'Node-10' },
      });
      expect(screen.queryByText('Node-2')).toBeNull();
      fireEvent.click(screen.getByRole('button', { name: 'Subscription order' }));
      const dialog = await screen.findByRole('dialog');
      expect(await within(dialog).findByText('Node-2')).toBeTruthy();
      expect(within(dialog).getByText('Node-10')).toBeTruthy();
      expect(
        vi.mocked(HttpUtil.post).mock.calls.some(([url]) => url.endsWith('/subSortIndex')),
      ).toBe(false);
    },
  );

  it('rechecks fresh metadata, saves only changed indices, and refreshes the inbound cache', async () => {
    const client = makeTestQueryClient();
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    const close = vi.fn();
    renderWithProviders(<InboundSortModal onClose={close} />, { queryClient: client });
    await screen.findByText('Node-2');
    // Traffic counters and API order do not invalidate the preview.
    vi.mocked(HttpUtil.get).mockResolvedValue(
      new Msg(
        true,
        '',
        [...rows].reverse().map((row) => ({ ...row, up: 42 })),
      ),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Save subscription order' }));
    await waitFor(() => expect(close).toHaveBeenCalledOnce());
    expect(HttpUtil.get).toHaveBeenCalledTimes(2);
    expect(HttpUtil.post).toHaveBeenCalledExactlyOnceWith(
      '/panel/api/inbounds/10/subSortIndex',
      { subSortIndex: 2 },
      { silent: true },
    );
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['inbounds'] });
  });

  it('does not write a stale preview after a remark changes', async () => {
    renderWithProviders(<InboundSortModal onClose={vi.fn()} />);
    await screen.findByText('Node-2');
    vi.mocked(HttpUtil.get).mockResolvedValue(
      new Msg(
        true,
        '',
        rows.map((row) => ({ ...row, remark: 'renamed' })),
      ),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Save subscription order' }));
    await screen.findByText(/Inbounds changed after this preview/);
    expect(HttpUtil.post).not.toHaveBeenCalled();
    expect(
      (screen.getByRole('button', { name: 'Save subscription order' }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
  });

  it('reports partial failure, blocks blind retries, and uses fresh indices when retrying', async () => {
    const initial = rows.map((row) => ({ ...row, subSortIndex: 9 }));
    vi.mocked(HttpUtil.get).mockResolvedValue(new Msg(true, '', initial));
    vi.mocked(HttpUtil.post)
      .mockResolvedValueOnce(new Msg(true))
      .mockResolvedValueOnce(new Msg(false, 'offline'));
    const close = vi.fn();
    renderWithProviders(<InboundSortModal onClose={close} />);
    await screen.findByText('Node-2');
    fireEvent.click(screen.getByRole('button', { name: 'Save subscription order' }));
    await screen.findByText(/Save interrupted after 1 confirmed updates/);
    expect(HttpUtil.post).toHaveBeenCalledTimes(2);
    expect(close).not.toHaveBeenCalled();
    expect(
      (screen.getByRole('button', { name: 'Save subscription order' }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    vi.mocked(HttpUtil.get).mockResolvedValue(
      new Msg(
        true,
        '',
        initial.map((row) => (row.id === 2 ? { ...row, subSortIndex: 1 } : row)),
      ),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Refresh preview' }));
    await screen.findByText('Node-2');
    fireEvent.click(screen.getByRole('button', { name: 'Save subscription order' }));
    await waitFor(() => expect(close).toHaveBeenCalledOnce());
    expect(HttpUtil.post).toHaveBeenCalledTimes(3);
    expect(HttpUtil.post).toHaveBeenLastCalledWith(
      '/panel/api/inbounds/10/subSortIndex',
      { subSortIndex: 2 },
      { silent: true },
    );
  });

  it('handles API failure before the first write', async () => {
    renderWithProviders(<InboundSortModal onClose={vi.fn()} />);
    await screen.findByText('Node-2');
    vi.mocked(HttpUtil.get).mockResolvedValue(new Msg(false, 'offline'));
    fireEvent.click(screen.getByRole('button', { name: 'Save subscription order' }));
    await screen.findByText(/Save interrupted after 0 confirmed updates/);
    expect(HttpUtil.post).not.toHaveBeenCalled();
  });

  it('requires refresh after an invalid response and disables saving an already ordered list', async () => {
    vi.mocked(HttpUtil.get).mockResolvedValue(new Msg(true, '', [...rows, rows[0]]));
    renderWithProviders(<InboundSortModal onClose={vi.fn()} />);
    await screen.findByText(/Could not load inbounds/);
    expect(
      (screen.getByRole('button', { name: 'Save subscription order' }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    vi.mocked(HttpUtil.get).mockResolvedValue(
      new Msg(
        true,
        '',
        rows.map((row) => ({ ...row, subSortIndex: row.id === 2 ? 1 : 2 })),
      ),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Refresh preview' }));
    await screen.findByText('Node-2');
    expect(
      (screen.getByRole('button', { name: 'Save subscription order' }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect(HttpUtil.post).not.toHaveBeenCalled();
  });
});

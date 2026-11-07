import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Modal, Radio, Space, Table, Typography } from 'antd';

import { HttpUtil } from '@/utils';
import { getMessage } from '@/utils/messageBus';
import { keys } from '@/api/queryKeys';
import {
  buildSubscriptionSort,
  SortableInboundsSchema,
  subscriptionSortSnapshot,
  type SortableInbound,
  type SortDirection,
} from './subscriptionSort';

async function fetchSortableInbounds(signal?: AbortSignal) {
  const msg = await HttpUtil.get('/panel/api/inbounds/list/slim', undefined, {
    silent: true,
    signal,
  });
  if (!msg.success) throw new Error(msg.msg);
  return SortableInboundsSchema.parse(msg.obj);
}

export default function InboundSortModal({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [rows, setRows] = useState<SortableInbound[]>([]);
  const [direction, setDirection] = useState<SortDirection>('asc');
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [blocked, setBlocked] = useState(false);
  const [error, setError] = useState('');
  const [progress, setProgress] = useState(0);
  const savingRef = useRef(false);
  const plan = useMemo(() => buildSubscriptionSort(rows, direction), [rows, direction]);
  const changed = plan.filter((row) => row.subSortIndex !== row.nextIndex);

  useEffect(() => {
    const controller = new AbortController();
    void fetchSortableInbounds(controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) setRows(result);
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setBlocked(true);
          setError(t('pages.inbounds.subscriptionSort.loadFailed'));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [revision, t]);

  const refresh = () => {
    setLoading(true);
    setRows([]);
    setError('');
    setBlocked(false);
    setRevision((value) => value + 1);
  };

  const save = async () => {
    if (savingRef.current || loading || blocked || changed.length === 0) return;
    savingRef.current = true;
    setSaving(true);
    setProgress(0);
    setError('');
    let completed = 0;
    let success = false;
    try {
      const current = await fetchSortableInbounds();
      if (subscriptionSortSnapshot(current) !== subscriptionSortSnapshot(rows)) {
        setBlocked(true);
        setError(t('pages.inbounds.subscriptionSort.stale'));
        return;
      }
      for (const row of changed) {
        // This endpoint changes only sort metadata, including on managed nodes.
        // Never submit the full inbound with potentially stale client settings.
        const msg = await HttpUtil.post(
          `/panel/api/inbounds/${row.id}/subSortIndex`,
          { subSortIndex: row.nextIndex },
          { silent: true },
        );
        if (!msg.success) throw new Error(msg.msg || `#${row.id}`);
        completed += 1;
        setProgress(completed);
      }
      success = true;
    } catch {
      setBlocked(true);
      setError(t('pages.inbounds.subscriptionSort.saveFailed', { count: completed }));
    } finally {
      await queryClient.invalidateQueries({ queryKey: keys.inbounds.root() });
      savingRef.current = false;
      setSaving(false);
    }
    if (success) {
      getMessage().success(t('pages.inbounds.subscriptionSort.saved'));
      onClose();
    }
  };

  return (
    <Modal
      open
      title={t('pages.inbounds.subscriptionSort.title')}
      width={760}
      onCancel={onClose}
      onOk={save}
      okText={t('pages.inbounds.subscriptionSort.save')}
      okButtonProps={{
        disabled: loading || blocked || changed.length === 0,
        'aria-label': t('pages.inbounds.subscriptionSort.save'),
      }}
      cancelButtonProps={{ disabled: saving }}
      confirmLoading={saving}
      closable={!saving}
      keyboard={!saving}
      mask={{ closable: !saving }}
    >
      <Space orientation="vertical" style={{ width: '100%' }} size="middle">
        <Alert type="info" title={t('pages.inbounds.subscriptionSort.help')} />
        <Space wrap>
          <Radio.Group
            value={direction}
            disabled={saving || loading}
            onChange={(event) => setDirection(event.target.value as SortDirection)}
            optionType="button"
            options={[
              { value: 'asc', label: t('pages.inbounds.subscriptionSort.asc') },
              { value: 'desc', label: t('pages.inbounds.subscriptionSort.desc') },
            ]}
          />
          <Button
            onClick={refresh}
            disabled={saving}
            loading={loading}
            aria-label={t('pages.inbounds.subscriptionSort.refresh')}
          >
            {t('pages.inbounds.subscriptionSort.refresh')}
          </Button>
          <Typography.Text>
            {t('pages.inbounds.subscriptionSort.scope', { count: rows.length })}
          </Typography.Text>
        </Space>
        {error && <Alert type="error" title={error} showIcon />}
        <Table
          size="small"
          rowKey="id"
          loading={loading}
          dataSource={plan}
          pagination={false}
          scroll={{ x: 480, y: 360 }}
          columns={[
            {
              title: t('pages.inbounds.subscriptionSort.newIndex'),
              dataIndex: 'nextIndex',
              width: 90,
            },
            { title: t('remark'), dataIndex: 'remark' },
            {
              title: t('pages.inbounds.subscriptionSort.oldIndex'),
              dataIndex: 'subSortIndex',
              width: 90,
            },
            { title: 'ID', dataIndex: 'id', width: 70 },
          ]}
        />
        <Typography.Text type="secondary">
          {saving
            ? t('pages.inbounds.subscriptionSort.progress', {
                count: progress,
                total: changed.length,
              })
            : t('pages.inbounds.subscriptionSort.pending', { count: changed.length })}
        </Typography.Text>
      </Space>
    </Modal>
  );
}

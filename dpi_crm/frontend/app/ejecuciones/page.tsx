'use client'

import { useCallback } from 'react'
import { Alert, Card, Descriptions, Space, Table, Typography } from 'antd'
import { api } from '@/lib/api-client'
import { fmtDateTime } from '@/lib/format'
import { JOB_KIND_LABEL, STAT_LABEL } from '@/lib/labels'
import type { Job } from '@/lib/types'
import { usePoll } from '@/lib/use-poll'
import { JobStatusTag } from '@/components/tags'

function duration(job: Job) {
  if (!job.started_at) return '—'
  const end = job.finished_at ? new Date(job.finished_at) : new Date()
  const s = Math.round((end.getTime() - new Date(job.started_at).getTime()) / 1000)
  return s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${s % 60} s`
}

export default function JobsPage() {
  const anyActive = useCallback((jobs?: Job[]) => !!jobs?.some((j) => ['queued', 'running'].includes(j.status)), [])
  const { data, loading, error } = usePoll(() => api.jobs(50), 3000, anyActive)

  return (
    <Space orientation="vertical" size="large" style={{ width: '100%' }}>
      <Typography.Title level={3} style={{ margin: 0 }}>Ejecuciones</Typography.Title>
      {error && <Alert type="error" showIcon title={error} />}
      <Card>
        <Table<Job>
          scroll={{ x: 'max-content' }}
          rowKey="id"
          loading={loading}
          dataSource={data}
          pagination={false}
          expandable={{
            expandedRowRender: (job) => (
              <Space orientation="vertical" style={{ width: '100%' }}>
                {job.error && <Alert type="error" showIcon title={job.error} />}
                {Object.keys(job.stats || {}).length > 0 && (
                  <Descriptions size="small" column={4}>
                    {Object.entries(job.stats).map(([k, v]) => (
                      <Descriptions.Item key={k} label={STAT_LABEL[k] ?? k}>{v}</Descriptions.Item>
                    ))}
                  </Descriptions>
                )}
                <pre style={{ margin: 0, maxHeight: 320, overflow: 'auto', fontSize: 12, background: '#f7f8fa', padding: 12, borderRadius: 8 }}>
                  {job.log || 'Sin registro todavía.'}
                </pre>
              </Space>
            ),
          }}
          columns={[
            { title: '#', dataIndex: 'id', width: 70 },
            { title: 'Automatización', dataIndex: 'kind', render: (k) => JOB_KIND_LABEL[k] ?? k },
            { title: 'Origen', dataIndex: 'trigger', width: 120, render: (t) => (t === 'cron' ? 'Programada' : 'Manual') },
            { title: 'Estado', dataIndex: 'status', width: 130, render: (s) => <JobStatusTag status={s} /> },
            { title: 'Creada', dataIndex: 'created_at', width: 180, render: fmtDateTime },
            { title: 'Duración', width: 120, render: (_, j) => duration(j) },
          ]}
        />
      </Card>
    </Space>
  )
}

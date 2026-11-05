'use client'

import { useCallback, useState } from 'react'
import Link from 'next/link'
import { Alert, App, Button, Card, Col, Descriptions, Row, Space, Statistic, Table, Typography } from 'antd'
import { SyncOutlined } from '@ant-design/icons'
import { api } from '@/lib/api-client'
import { ago, fmtDateTime } from '@/lib/format'
import { JOB_KIND_LABEL } from '@/lib/labels'
import type { NetworkEvent, Page, Status } from '@/lib/types'
import { usePoll } from '@/lib/use-poll'
import { CategoryTag, JobStatusTag } from '@/components/tags'

const isActive = (s?: Status) => !!s && (s.runner_busy || ['queued', 'running'].includes(s.last_job?.status ?? ''))

export default function HomePage() {
  const { message } = App.useApp()
  const [starting, setStarting] = useState(false)
  const status = usePoll(api.status, 3000, isActive)
  const events = usePoll<Page<NetworkEvent>>(
    () => api.events({ page_size: 8 }),
    5000,
    useCallback(() => isActive(status.data), [status.data]),
  )
  const s = status.data

  async function sync() {
    setStarting(true)
    try {
      await api.startNetworkSync()
      message.success('Sincronización en marcha')
      status.refresh()
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e))
    } finally {
      setStarting(false)
    }
  }

  return (
    <Space orientation="vertical" size="large" style={{ width: '100%' }}>
      <Row justify="space-between" align="middle">
        <Typography.Title level={3} style={{ margin: 0 }}>Inicio</Typography.Title>
        <Button type="primary" icon={<SyncOutlined spin={isActive(s)} />} loading={starting}
          disabled={isActive(s)} onClick={sync}>
          {isActive(s) ? 'Sincronizando…' : 'Sincronizar ahora'}
        </Button>
      </Row>
      {status.error && <Alert type="error" showIcon title="No se puede hablar con el backend" description={status.error} />}
      {s && !s.mcp_reachable && (
        <Alert type="warning" showIcon title="El servidor MCP no responde"
          description={`Arráncalo para que las automatizaciones puedan usar LinkedIn (${s.mcp_url}).`} />
      )}
      <Row gutter={[16, 16]}>
        <Col xs={24} md={6}><Card><Statistic title="Contactos" value={s?.contacts ?? 0} loading={status.loading} /></Card></Col>
        <Col xs={24} md={6}><Card><Statistic title="Novedades (últimas 24 h)" value={s?.events_last_24h ?? 0} loading={status.loading} /></Card></Col>
        <Col xs={24} md={6}><Card><Statistic title="Novedades en total" value={s?.events ?? 0} loading={status.loading} /></Card></Col>
        <Col xs={24} md={6}><Card><Statistic title="Próxima sincronización" value={s?.next_sync_at ? fmtDateTime(s.next_sync_at) : 'Cron apagado'} styles={{ content: { fontSize: 18 } }} loading={status.loading} /></Card></Col>
      </Row>
      <Row gutter={[16, 16]}>
        <Col xs={24} lg={10}>
          <Card title="Última ejecución" extra={<Link href="/ejecuciones">Ver todas</Link>}>
            {s?.last_job ? (
              <Descriptions column={1} size="small">
                <Descriptions.Item label="Automatización">{JOB_KIND_LABEL[s.last_job.kind]}</Descriptions.Item>
                <Descriptions.Item label="Estado"><JobStatusTag status={s.last_job.status} /></Descriptions.Item>
                <Descriptions.Item label="Origen">{s.last_job.trigger === 'cron' ? 'Programada' : 'Manual'}</Descriptions.Item>
                <Descriptions.Item label="Empezó">{ago(s.last_job.started_at ?? s.last_job.created_at)}</Descriptions.Item>
                {s.last_job.error && <Descriptions.Item label="Error">{s.last_job.error}</Descriptions.Item>}
              </Descriptions>
            ) : (
              <Typography.Text type="secondary">Todavía no se ha ejecutado nada.</Typography.Text>
            )}
          </Card>
        </Col>
        <Col xs={24} lg={14}>
          <Card title="Novedades recientes" extra={<Link href="/novedades">Ver todas</Link>}>
            <Table<NetworkEvent>
              scroll={{ x: 'max-content' }}
              rowKey="id"
              size="small"
              showHeader={false}
              pagination={false}
              loading={events.loading}
              dataSource={events.data?.items ?? []}
              locale={{ emptyText: 'Sin novedades. Pulsa «Sincronizar ahora».' }}
              columns={[
                { dataIndex: 'category', width: 150, render: (c) => <CategoryTag category={c} /> },
                {
                  dataIndex: 'contact_name',
                  render: (n, e) => (
                    <Space orientation="vertical" size={0}>
                      <Link href={`/contactos/${e.contact_id}`}>{n}</Link>
                      <Typography.Text type="secondary">{e.detail}</Typography.Text>
                    </Space>
                  ),
                },
              ]}
            />
          </Card>
        </Col>
      </Row>
    </Space>
  )
}

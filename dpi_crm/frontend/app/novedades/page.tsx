'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { Card, Segmented, Space, Table, Typography } from 'antd'
import { api } from '@/lib/api-client'
import { fmtDateTime } from '@/lib/format'
import { CATEGORY_LABEL } from '@/lib/labels'
import type { NetworkEvent, Page } from '@/lib/types'
import { CategoryTag } from '@/components/tags'

const OPTIONS = [{ label: 'Todas', value: '' }, ...Object.entries(CATEGORY_LABEL).map(([value, label]) => ({ label, value }))]

export default function EventsPage() {
  const [category, setCategory] = useState('')
  const [page, setPage] = useState(1)
  const [data, setData] = useState<Page<NetworkEvent>>()
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    api.events({ category, page, page_size: 25 }).then(setData).finally(() => setLoading(false))
  }, [category, page])

  return (
    <Space orientation="vertical" size="large" style={{ width: '100%' }}>
      <Typography.Title level={3} style={{ margin: 0 }}>Novedades de la red</Typography.Title>
      <Card>
        <Segmented options={OPTIONS} value={category} onChange={(v) => { setCategory(String(v)); setPage(1) }} style={{ marginBottom: 16 }} />
        <Table<NetworkEvent>
          scroll={{ x: 'max-content' }}
          rowKey="id"
          loading={loading}
          dataSource={data?.items}
          pagination={{ current: page, pageSize: 25, total: data?.total, onChange: setPage, showSizeChanger: false }}
          columns={[
            { title: 'Tipo', dataIndex: 'category', width: 160, render: (c) => <CategoryTag category={c} /> },
            { title: 'Persona', dataIndex: 'contact_name', render: (n, e) => <Link href={`/contactos/${e.contact_id}`}>{n}</Link> },
            { title: 'Empresa', dataIndex: 'contact_company', render: (c) => c || '—' },
            { title: 'Detalle', dataIndex: 'detail' },
            { title: 'Publicación', dataIndex: 'post_url', width: 110, render: (u) => (u ? <a href={u} target="_blank" rel="noreferrer">Abrir</a> : '—') },
            { title: 'Vista', dataIndex: 'first_seen_at', width: 170, render: fmtDateTime },
          ]}
        />
      </Card>
    </Space>
  )
}

'use client'

import { Suspense, useEffect, useState } from 'react'
import Link from 'next/link'
import { useRouter, useSearchParams } from 'next/navigation'
import { Card, Input, Segmented, Space, Table, Tag, Typography } from 'antd'
import { LinkedinOutlined } from '@ant-design/icons'
import { api } from '@/lib/api-client'
import { fmtMonth } from '@/lib/format'
import type { Contact, Page, SavedCompany } from '@/lib/types'
import { SourceTag } from '@/components/tags'

function ContactsTable() {
  const params = useSearchParams()
  const router = useRouter()
  const companyId = params.get('company_id') ? Number(params.get('company_id')) : undefined
  const [q, setQ] = useState('')
  const [source, setSource] = useState('')
  const [page, setPage] = useState(1)
  const [data, setData] = useState<Page<Contact>>()
  const [loading, setLoading] = useState(true)
  const [company, setCompany] = useState<SavedCompany>()

  useEffect(() => {
    if (companyId) api.company(companyId).then(setCompany)
    else setCompany(undefined)
  }, [companyId])

  useEffect(() => {
    setLoading(true)
    const t = setTimeout(() => {
      api.contacts({ q, source, company_id: companyId, page, page_size: 50 }).then(setData).finally(() => setLoading(false))
    }, 250)
    return () => clearTimeout(t)
  }, [q, source, page, companyId])

  return (
    <Card>
      <Space wrap style={{ marginBottom: 16 }}>
        {company && (
          <Tag closable color="blue" onClose={() => router.push('/contactos')} style={{ padding: '4px 8px' }}>
            Empresa: {company.name}
          </Tag>
        )}
        <Input.Search placeholder="Nombre, empresa, puesto o email" allowClear style={{ width: 320 }}
          onChange={(e) => { setQ(e.target.value); setPage(1) }} />
        <Segmented value={source} onChange={(v) => { setSource(String(v)); setPage(1) }}
          options={[{ label: 'Todos', value: '' }, { label: 'Red', value: 'network' }, { label: 'Sales Nav', value: 'salesnav' }]} />
      </Space>
      <Table<Contact>
        scroll={{ x: 'max-content' }}
        rowKey="id"
        loading={loading}
        dataSource={data?.items}
        pagination={{ current: page, pageSize: 50, total: data?.total, onChange: setPage, showSizeChanger: false }}
        columns={[
          { title: 'Nombre', dataIndex: 'full_name', render: (n, c) => <Link href={`/contactos/${c.id}`}>{n}</Link> },
          { title: 'Puesto', dataIndex: 'title', ellipsis: true, render: (t, c) => t || c.headline || '—' },
          {
            title: 'Empresa', dataIndex: 'company',
            render: (v, c) => (c.company_id ? <Link href={`/contactos?company_id=${c.company_id}`}>{v}</Link> : v || '—'),
          },
          { title: 'Desde', dataIndex: 'company_since', width: 110, render: (v) => fmtMonth(v) ?? '—' },
          { title: 'Email', dataIndex: 'email', render: (v) => v || '—' },
          { title: 'Teléfono', dataIndex: 'phone', width: 140, render: (v) => v || '—' },
          { title: 'Origen', dataIndex: 'source', width: 100, render: (s) => <SourceTag source={s} /> },
          {
            title: '', width: 50,
            render: (_, c) => {
              const url = c.profile_url || c.sales_nav_url
              return url ? <a href={url} target="_blank" rel="noreferrer" aria-label="Abrir en LinkedIn"><LinkedinOutlined /></a> : null
            },
          },
        ]}
      />
    </Card>
  )
}

export default function ContactsPage() {
  return (
    <Space orientation="vertical" size="large" style={{ width: '100%' }}>
      <Typography.Title level={3} style={{ margin: 0 }}>Contactos</Typography.Title>
      <Suspense>
        <ContactsTable />
      </Suspense>
    </Space>
  )
}

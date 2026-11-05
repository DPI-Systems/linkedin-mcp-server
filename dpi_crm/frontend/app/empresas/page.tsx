'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { Alert, App, Button, Card, Col, Form, Input, InputNumber, Row, Select, Space, Switch, Table, Typography } from 'antd'
import { CloudDownloadOutlined, SearchOutlined } from '@ant-design/icons'
import { api } from '@/lib/api-client'
import { FUNCTION_OPTIONS, SENIORITY_OPTIONS } from '@/lib/labels'
import type { CompanyCandidate, LeadFilters, SavedCompany } from '@/lib/types'

type ImportForm = LeadFilters & { max_results: number }

export default function CompaniesPage() {
  const { message } = App.useApp()
  const router = useRouter()
  const [name, setName] = useState('')
  const [results, setResults] = useState<CompanyCandidate[]>()
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState<string>()
  const [selected, setSelected] = useState<string[]>([])
  const [importing, setImporting] = useState(false)
  const [saved, setSaved] = useState<SavedCompany[]>([])
  const [form] = Form.useForm<ImportForm>()

  const [savedQuery, setSavedQuery] = useState('')
  useEffect(() => {
    const t = setTimeout(() => api.companies(savedQuery).then(setSaved), 250)
    return () => clearTimeout(t)
  }, [savedQuery])

  async function search() {
    if (name.trim().length < 2) return
    setSearching(true)
    setSearchError(undefined)
    setSelected([])
    try {
      setResults(await api.searchCompanies(name.trim()))
    } catch (e) {
      setSearchError(e instanceof Error ? e.message : String(e))
    } finally {
      setSearching(false)
    }
  }

  async function startImport(values: ImportForm) {
    const companies = (results ?? []).filter((c) => selected.includes(c.company_id))
    const { max_results, ...filters } = values
    setImporting(true)
    try {
      const job = await api.startCompanyImport(companies, max_results, filters)
      message.success(`Importación #${job.id} en marcha`)
      router.push('/ejecuciones')
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e))
    } finally {
      setImporting(false)
    }
  }

  return (
    <Space orientation="vertical" size="large" style={{ width: '100%' }}>
      <Typography.Title level={3} style={{ margin: 0 }}>Empresas</Typography.Title>
      <Card title="Buscar en Sales Navigator">
        <Space.Compact style={{ width: '100%', maxWidth: 520, marginBottom: 16 }}>
          <Input placeholder="Seanto" value={name} onChange={(e) => setName(e.target.value)} onPressEnter={search} />
          <Button type="primary" icon={<SearchOutlined />} loading={searching} onClick={search}>Buscar</Button>
        </Space.Compact>
        {searching && <Typography.Paragraph type="secondary">Buscando en LinkedIn, tarda unos segundos…</Typography.Paragraph>}
        {searchError && <Alert type="error" showIcon title={searchError} style={{ marginBottom: 16 }} />}
        {results && (
          <Table<CompanyCandidate>
            scroll={{ x: 'max-content' }}
            rowKey="company_id"
            size="middle"
            dataSource={results}
            pagination={false}
            rowSelection={{ selectedRowKeys: selected, onChange: (keys) => setSelected(keys as string[]) }}
            locale={{ emptyText: 'Ninguna empresa con ese nombre' }}
            columns={[
              { title: 'Empresa', dataIndex: 'name', render: (n, c) => (c.sales_nav_url ? <a href={c.sales_nav_url} target="_blank" rel="noreferrer">{n}</a> : n) },
              { title: 'Sector', dataIndex: 'industry', render: (v) => v || '—' },
              { title: 'Empleados', dataIndex: 'employees', render: (v) => v ?? '—' },
              { title: 'Ubicación', dataIndex: 'location', render: (v) => v || '—' },
              { title: 'Descripción', dataIndex: 'description', ellipsis: true, render: (v) => v || '—' },
            ]}
          />
        )}
      </Card>

      {selected.length > 0 && (
        <Card title={`Importar contactos de ${selected.length} empresa${selected.length > 1 ? 's' : ''}`}>
          <Form form={form} layout="vertical" initialValues={{ max_results: 25 }} onFinish={startImport}>
            <Row gutter={16}>
              <Col xs={24} md={6}><Form.Item name="max_results" label="Máximo por empresa"><InputNumber min={1} max={100} style={{ width: '100%' }} /></Form.Item></Col>
              <Col xs={24} md={9}><Form.Item name="titles" label="Puestos (texto libre)"><Select mode="tags" placeholder="CTO, Director de compras" tokenSeparators={[',']} /></Form.Item></Col>
              <Col xs={24} md={9}><Form.Item name="keywords" label="Palabras clave"><Input placeholder="IA" /></Form.Item></Col>
              <Col xs={24} md={8}><Form.Item name="seniority" label="Nivel"><Select mode="multiple" options={SENIORITY_OPTIONS} allowClear /></Form.Item></Col>
              <Col xs={24} md={8}><Form.Item name="functions" label="Área"><Select mode="multiple" options={FUNCTION_OPTIONS} allowClear /></Form.Item></Col>
              <Col xs={12} md={4}><Form.Item name="posted_recently" label="Publicó hace poco" valuePropName="checked"><Switch /></Form.Item></Col>
              <Col xs={12} md={4}><Form.Item name="changed_jobs_recently" label="Cambió de empleo" valuePropName="checked"><Switch /></Form.Item></Col>
            </Row>
            <Button type="primary" htmlType="submit" icon={<CloudDownloadOutlined />} loading={importing}>Importar contactos</Button>
          </Form>
        </Card>
      )}

      <Card title={`Empresas en el CRM (${saved.length})`} extra={<Input.Search allowClear placeholder="Filtrar" onChange={(e) => setSavedQuery(e.target.value)} style={{ width: 220 }} />}>
        <Table<SavedCompany>
          scroll={{ x: 'max-content' }}
          rowKey="id"
          dataSource={saved}
          pagination={{ pageSize: 25, hideOnSinglePage: true }}
          locale={{ emptyText: 'Todavía no hay empresas' }}
          columns={[
            { title: 'Empresa', dataIndex: 'name', render: (n, c) => (c.linkedin_url ? <a href={c.linkedin_url} target="_blank" rel="noreferrer">{n}</a> : n) },
            { title: 'Sector', dataIndex: 'industry', render: (v) => v || '—' },
            { title: 'Empleados', dataIndex: 'employees', render: (v) => v || '—' },
            { title: 'Contactos', dataIndex: 'contacts', render: (n, c) => <Link href={`/contactos?company_id=${c.id}`}>{n}</Link> },
          ]}
        />
      </Card>
    </Space>
  )
}

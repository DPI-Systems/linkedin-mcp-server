'use client'

import { use, useEffect, useState } from 'react'
import Link from 'next/link'
import { App, Breadcrumb, Button, Card, Col, Descriptions, Empty, Form, Input, Row, Space, Spin, Timeline, Typography } from 'antd'
import { LinkedinOutlined } from '@ant-design/icons'
import { api } from '@/lib/api-client'
import { fmtDateTime, fmtMonth } from '@/lib/format'
import type { ContactDetail } from '@/lib/types'
import { CategoryTag, SourceTag } from '@/components/tags'

type FormValues = { email?: string; phone?: string; website?: string; notes?: string }

export default function ContactPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params)
  const { message } = App.useApp()
  const [contact, setContact] = useState<ContactDetail>()
  const [error, setError] = useState<string>()
  const [saving, setSaving] = useState(false)
  const [form] = Form.useForm<FormValues>()

  useEffect(() => {
    api.contact(Number(id)).then((c) => {
      setContact(c)
      form.setFieldsValue({ email: c.email ?? '', phone: c.phone ?? '', website: c.website ?? '', notes: c.notes ?? '' })
    }).catch((e) => setError(e.message))
  }, [id, form])

  async function save(values: FormValues) {
    setSaving(true)
    try {
      setContact(await api.updateContact(Number(id), values))
      message.success('Guardado')
    } catch (e) {
      message.error(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  if (error) return <Empty description={error} />
  if (!contact) return <Spin />
  const since = fmtMonth(contact.company_since)

  return (
    <Space orientation="vertical" size="large" style={{ width: '100%' }}>
      <Breadcrumb items={[{ title: <Link href="/contactos">Contactos</Link> }, { title: contact.full_name }]} />
      <Row justify="space-between" align="middle">
        <Space orientation="vertical" size={0}>
          <Typography.Title level={3} style={{ margin: 0 }}>{contact.full_name}</Typography.Title>
          <Typography.Text type="secondary">{contact.title || contact.headline}</Typography.Text>
        </Space>
        <Space>
          {contact.profile_url && <Button icon={<LinkedinOutlined />} href={contact.profile_url} target="_blank">Perfil</Button>}
          {contact.sales_nav_url && <Button href={contact.sales_nav_url} target="_blank">Sales Navigator</Button>}
        </Space>
      </Row>
      <Row gutter={[16, 16]}>
        <Col xs={24} lg={14}>
          <Card title="Datos de LinkedIn" extra={<Typography.Text type="secondary">Solo lectura · {contact.profile_synced_at ? `leído ${fmtDateTime(contact.profile_synced_at)}` : 'perfil sin leer'}</Typography.Text>}>
            <Descriptions column={1} size="small">
              <Descriptions.Item label="Empresa">
                {contact.company_id ? <Link href={`/contactos?company_id=${contact.company_id}`}>{contact.company}</Link> : contact.company || '—'}
              </Descriptions.Item>
              <Descriptions.Item label="En la empresa desde">{since ?? contact.company_since_text ?? '—'}</Descriptions.Item>
              <Descriptions.Item label="Puesto">{contact.title || '—'}</Descriptions.Item>
              <Descriptions.Item label="Titular">{contact.headline || '—'}</Descriptions.Item>
              <Descriptions.Item label="Ubicación">{contact.location || '—'}</Descriptions.Item>
              <Descriptions.Item label="Origen"><SourceTag source={contact.source} /></Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>
        <Col xs={24} lg={10}>
          <Card title="Datos de contacto" extra={<Typography.Text type="secondary">Editables · LinkedIn solo rellena los vacíos</Typography.Text>}>
            <Form form={form} layout="vertical" onFinish={save}>
              <Form.Item name="email" label="Email" rules={[{ type: 'email', message: 'No es un email válido' }]}>
                <Input placeholder="nombre@empresa.com" />
              </Form.Item>
              <Form.Item name="phone" label="Teléfono"><Input placeholder="+34 600 000 000" /></Form.Item>
              <Form.Item name="website" label="Web"><Input placeholder="https://empresa.com" /></Form.Item>
              <Form.Item name="notes" label="Notas"><Input.TextArea rows={4} /></Form.Item>
              <Button type="primary" htmlType="submit" loading={saving}>Guardar</Button>
            </Form>
          </Card>
        </Col>
      </Row>
      {contact.contact_info_text && (
        <Card title="Información de contacto en LinkedIn" extra={<Typography.Text type="secondary">{fmtDateTime(contact.contact_info_synced_at)}</Typography.Text>}>
          <Typography.Paragraph style={{ whiteSpace: 'pre-line', marginBottom: 0 }}>
            {contact.contact_info_text.split('\n').filter((l) => l.trim()).join('\n')}
          </Typography.Paragraph>
        </Card>
      )}
      <Card title="Historial de novedades">
        {contact.events.length ? (
          <Timeline
            items={contact.events.map((e) => ({
              children: (
                <Space orientation="vertical" size={0}>
                  <Space><CategoryTag category={e.category} /><Typography.Text type="secondary">{fmtDateTime(e.first_seen_at)}</Typography.Text></Space>
                  <span>{e.detail} {e.post_url && <a href={e.post_url} target="_blank" rel="noreferrer">· publicación</a>}</span>
                </Space>
              ),
            }))}
          />
        ) : (
          <Typography.Text type="secondary">Sin novedades registradas.</Typography.Text>
        )}
      </Card>
    </Space>
  )
}

'use client'

import { Tag } from 'antd'
import { CATEGORY_COLOR, CATEGORY_LABEL, JOB_STATUS } from '@/lib/labels'
import type { Category, JobStatus } from '@/lib/types'

export function CategoryTag({ category }: { category: Category }) {
  return <Tag color={CATEGORY_COLOR[category] ?? 'default'}>{CATEGORY_LABEL[category] ?? category}</Tag>
}

export function JobStatusTag({ status }: { status: JobStatus }) {
  const s = JOB_STATUS[status]
  return <Tag color={s?.color}>{s?.label ?? status}</Tag>
}

export function SourceTag({ source }: { source: string }) {
  return source === 'salesnav' ? <Tag color="geekblue">Sales Nav</Tag> : <Tag color="cyan">Red</Tag>
}

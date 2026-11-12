<template>
  <section class="page" data-module="datastore">
    <header class="page-head">
      <div>
        <h2>数据集管理管理</h2>
        <p class="page-desc">维护数据集定义的输入、执行约束与结果记录，为数据集管理提供可验证的数据工程流程。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="toggleCreate">登记数据集定义</button>
        <button class="btn" type="button" @click="exportRows">导出数据集管理清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form v-if="creating" class="create-panel" @submit.prevent="submitCreate">
      <label v-for="field in columns" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="createForm[field]" :placeholder="`请输入${field}`" />
      </label>
      <button class="btn primary" type="submit">提交登记</button>
      <button class="btn ghost" type="button" @click="toggleCreate">取消</button>
    </form>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>数据集编号</span>
        <input v-model="filters.keyword" placeholder="按数据集编号检索" />
      </label>
      <label class="filter-item">
        <span>状态</span>
        <select v-model="filters.status">
          <option value="">全部</option>
          <option v-for="item in statuses" :key="item" :value="item">{{ item }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>状态</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td>{{ row.status ?? '—' }}</td>
          <td class="row-actions">
            <button class="link" type="button" @click="openDetail(row)">详情</button>
            <button
              v-for="action in actionsFor(row)"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <span v-if="!actionsFor(row).length" class="muted-text">已终态</span>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 2" class="empty-state">暂无数据集管理数据，可先登记数据集定义</td>
        </tr>
      </tbody>
    </table>

    <section v-if="detail" class="detail-panel">
      <header class="detail-head">
        <h3>数据集详情：{{ detail['数据集编号'] }}（{{ detail.status }}）</h3>
        <button class="btn ghost" type="button" @click="detail = null">关闭</button>
      </header>
      <h4>关联分享授权</h4>
      <table class="data-table">
        <thead>
          <tr>
            <th>授权编号</th>
            <th>链接编号</th>
            <th>裁决动作</th>
            <th>裁决结果</th>
            <th>裁决时间</th>
            <th>说明</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="record in linkedDecisions" :key="String(record['授权编号'])">
            <td>{{ record['授权编号'] }}</td>
            <td>{{ record['链接编号'] }}</td>
            <td>{{ record['裁决动作'] }}</td>
            <td>{{ record['裁决结果'] }}</td>
            <td>{{ record['裁决时间'] }}</td>
            <td>{{ record['说明'] }}</td>
          </tr>
          <tr v-if="!linkedDecisions.length">
            <td colspan="6" class="empty-state">暂无关联的分享授权记录</td>
          </tr>
        </tbody>
      </table>
    </section>

    <footer class="page-foot">
      <span>共 {{ total }} 条数据集管理记录</span>
      <span v-if="noticeMessage" class="notice-text">{{ noticeMessage }}</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, any>
type Decision = Record<string, any>

const ENDPOINT = '/api/datastore'
const columns = ["数据集编号", "数据集名称", "来源表名", "字段列表", "更新策略", "数据采样比例", "生命周期天数", "缓存开关"]
const statuses = ["草稿", "待审核", "已生效", "已废弃"]
// 数据集生命周期：草稿→待审核→已生效→已废弃，每个状态只放出当前可执行的动作
const STATUS_ACTIONS: Record<string, string[]> = {
  '草稿': ['提交审核', '废弃下线'],
  '待审核': ['生效发布', '废弃下线'],
  '已生效': ['废弃下线'],
  '已废弃': [],
}

const rows = ref<Row[]>([])
const total = ref(0)
const stats = ref([
  { label: '数据集定义总数', value: 0 },
  { label: '已生效数据集', value: 0 },
  { label: '待审核数据集', value: 0 },
])
const errorMessage = ref('')
const noticeMessage = ref('')
const filters = ref({ keyword: '', status: '' })
const creating = ref(false)
const createForm = ref<Record<string, string>>({})
const detail = ref<Row | null>(null)

const linkedDecisions = computed<Decision[]>(() => (detail.value?.['关联分享授权'] as Decision[] | undefined) ?? [])

function actionsFor(row: Row): string[] {
  return STATUS_ACTIONS[String(row.status)] ?? []
}

function resetFilters() {
  filters.value = { keyword: '', status: '' }
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function toggleCreate() {
  creating.value = !creating.value
}

async function parseError(response: Response, fallback: string): Promise<string> {
  try {
    const data = await response.json()
    if (data?.detail) {
      return typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)
    }
  } catch {
    // 响应体不是 JSON 时用兜底文案
  }
  return fallback
}

async function submitCreate() {
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    const response = await request(ENDPOINT, {
      method: 'POST',
      body: JSON.stringify({ values: createForm.value }),
    })
    if (!response.ok) {
      throw new Error(await parseError(response, '数据集定义登记失败'))
    }
    const payload = await response.json()
    noticeMessage.value = payload.message ?? '数据集定义已登记'
    createForm.value = {}
    creating.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据集定义登记失败'
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    // 动作契约：动作名放顶层
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error(await parseError(response, `动作「${action}」未生效`))
    }
    const payload = await response.json()
    noticeMessage.value = payload.message ?? `动作「${action}」已执行`
    await reload()
    if (detail.value && detail.value.id === row.id) {
      await openDetail(row)
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据集管理操作失败'
  }
}

async function openDetail(row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}`)
    if (!response.ok) {
      throw new Error(await parseError(response, '数据集定义详情读取失败'))
    }
    detail.value = await response.json()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据集定义详情读取失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const params = new URLSearchParams()
  if (filters.value.keyword) {
    params.set('keyword', filters.value.keyword)
  }
  if (filters.value.status) {
    params.set('status', filters.value.status)
  }
  try {
    const [listResponse, allResponse, activeResponse, reviewResponse] = await Promise.all([
      request(`${ENDPOINT}?${params.toString()}`),
      request(`${ENDPOINT}?size=1`),
      request(`${ENDPOINT}?status=${encodeURIComponent('已生效')}&size=1`),
      request(`${ENDPOINT}?status=${encodeURIComponent('待审核')}&size=1`),
    ])
    if (!listResponse.ok) {
      throw new Error(await parseError(listResponse, '数据集定义列表读取失败'))
    }
    const payload = await listResponse.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    const allTotal = allResponse.ok ? (await allResponse.json()).total ?? 0 : 0
    const activeTotal = activeResponse.ok ? (await activeResponse.json()).total ?? 0 : 0
    const reviewTotal = reviewResponse.ok ? (await reviewResponse.json()).total ?? 0 : 0
    stats.value = [
      { label: '数据集定义总数', value: allTotal },
      { label: '已生效数据集', value: activeTotal },
      { label: '待审核数据集', value: reviewTotal },
    ]
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据集管理列表读取失败'
  }
}

onMounted(reload)
</script>

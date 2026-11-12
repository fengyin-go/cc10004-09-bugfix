<template>
  <section class="page" data-module="share">
    <header class="page-head">
      <div>
        <h2>数据分享管理</h2>
        <p class="page-desc">维护数据分享链接的输入、执行约束与结果记录，为数据分享提供可验证的数据工程流程。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="toggleCreate">登记分享链接</button>
        <button class="btn" type="button" @click="exportRows">导出数据分享清单</button>
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
        <span>链接编号</span>
        <input v-model="filters.keyword" placeholder="按链接编号检索" />
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
          <th>异常说明</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td>{{ row.status ?? '—' }}</td>
          <td>{{ row['失效原因'] ?? row['未解析原因'] ?? '—' }}</td>
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
          <td :colspan="columns.length + 3" class="empty-state">暂无数据分享数据，可先登记分享链接</td>
        </tr>
      </tbody>
    </table>

    <section v-if="detail" class="detail-panel">
      <header class="detail-head">
        <h3>链接详情：{{ detail['链接编号'] }}（{{ detail.status }}）</h3>
        <button class="btn ghost" type="button" @click="detail = null">关闭</button>
      </header>
      <p v-if="detail['失效原因']" class="error-text">失效原因：{{ detail['失效原因'] }}</p>
      <p v-if="detail['未解析原因']" class="error-text">未解析原因：{{ detail['未解析原因'] }}</p>
      <p v-if="detail['授权到期时间']" class="muted-text">授权到期时间：{{ detail['授权到期时间'] }}</p>
      <form v-if="detail['未解析原因'] && detail.status === '草稿'" class="filter-bar" @submit.prevent="rebind">
        <label class="filter-item">
          <span>重新绑定目标数据集</span>
          <input v-model="rebindTarget" placeholder="请输入数据集编号，如 DATA-0003" />
        </label>
        <button class="btn" type="submit">重新绑定并提交生成</button>
      </form>
      <h4>授权轨迹</h4>
      <table class="data-table">
        <thead>
          <tr>
            <th>授权编号</th>
            <th>裁决动作</th>
            <th>裁决结果</th>
            <th>数据集</th>
            <th>裁决时间</th>
            <th>说明</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="record in detailDecisions" :key="String(record['授权编号'])">
            <td>{{ record['授权编号'] }}</td>
            <td>{{ record['裁决动作'] }}</td>
            <td>{{ record['裁决结果'] }}</td>
            <td>{{ record['数据集编号'] ?? '—' }} {{ record['数据集名称'] ?? '' }}</td>
            <td>{{ record['裁决时间'] }}</td>
            <td>{{ record['说明'] }}</td>
          </tr>
          <tr v-if="!detailDecisions.length">
            <td colspan="6" class="empty-state">暂无授权裁决记录</td>
          </tr>
        </tbody>
      </table>
    </section>

    <footer class="page-foot">
      <span>共 {{ total }} 条数据分享记录</span>
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

const ENDPOINT = '/api/share'
const columns = ["链接编号", "目标数据集", "访问密码", "有效期天数", "最大浏览次数", "下载开关", "水印内容", "过期自动清理"]
const statuses = ["草稿", "待生成", "已生效", "已失效"]
// 链接生命周期：草稿→待生成→已生效→已失效，每个状态只放出当前可执行的动作
const STATUS_ACTIONS: Record<string, string[]> = {
  '草稿': ['提交生成', '撤销链接'],
  '待生成': ['生效分享', '撤销链接'],
  '已生效': ['撤销链接'],
  '已失效': [],
}

const rows = ref<Row[]>([])
const total = ref(0)
const stats = ref([
  { label: '分享链接总数', value: 0 },
  { label: '已生效链接', value: 0 },
  { label: '待生成链接', value: 0 },
])
const errorMessage = ref('')
const noticeMessage = ref('')
const filters = ref({ keyword: '', status: '' })
const creating = ref(false)
const createForm = ref<Record<string, string>>({})
const detail = ref<Row | null>(null)
const rebindTarget = ref('')

const detailDecisions = computed<Decision[]>(() => (detail.value?.['授权轨迹'] as Decision[] | undefined) ?? [])

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
      throw new Error(await parseError(response, '分享链接登记失败'))
    }
    const payload = await response.json()
    noticeMessage.value = payload.message ?? '分享链接已登记'
    createForm.value = {}
    creating.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '分享链接登记失败'
  }
}

async function runAction(action: string, row: Row, values?: Record<string, string>) {
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    // 动作契约：动作名放顶层，重新绑定等补充字段放 values
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify(values ? { action, values } : { action }),
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
    errorMessage.value = error instanceof Error ? error.message : '数据分享操作失败'
  }
}

async function rebind() {
  if (!detail.value || !rebindTarget.value.trim()) {
    return
  }
  await runAction('提交生成', detail.value, { 目标数据集: rebindTarget.value.trim() })
  rebindTarget.value = ''
}

async function openDetail(row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}`)
    if (!response.ok) {
      throw new Error(await parseError(response, '分享链接详情读取失败'))
    }
    detail.value = await response.json()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '分享链接详情读取失败'
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
    const [listResponse, allResponse, activeResponse, pendingResponse] = await Promise.all([
      request(`${ENDPOINT}?${params.toString()}`),
      request(`${ENDPOINT}?size=1`),
      request(`${ENDPOINT}?status=${encodeURIComponent('已生效')}&size=1`),
      request(`${ENDPOINT}?status=${encodeURIComponent('待生成')}&size=1`),
    ])
    if (!listResponse.ok) {
      throw new Error(await parseError(listResponse, '分享链接列表读取失败'))
    }
    const payload = await listResponse.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    const allTotal = allResponse.ok ? (await allResponse.json()).total ?? 0 : 0
    const activeTotal = activeResponse.ok ? (await activeResponse.json()).total ?? 0 : 0
    const pendingTotal = pendingResponse.ok ? (await pendingResponse.json()).total ?? 0 : 0
    stats.value = [
      { label: '分享链接总数', value: allTotal },
      { label: '已生效链接', value: activeTotal },
      { label: '待生成链接', value: pendingTotal },
    ]
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据分享列表读取失败'
  }
}

onMounted(reload)
</script>

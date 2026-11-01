<script setup>
import { computed, nextTick, onBeforeUnmount, ref } from 'vue'
// 按需引入：完整 echarts 约 1.1MB，这里只注册用到的图型，体积降到 1/3
import * as echarts from 'echarts/core'
import { BarChart, LineChart, PieChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([BarChart, LineChart, PieChart, GridComponent, TooltipComponent, CanvasRenderer])

const EXAMPLES = [
  '上个月各门店的营收，按实收降序',
  '最近 30 天销量前 5 的商品',
  '各渠道的订单量和客单价',
  '退款率是多少',
  '最近 7 天每天的订单量'
]

const question = ref('')
const loading = ref(false)
const error = ref('')
const clarify = ref('')
const clarifyOptions = ref([])
const sessionId = ref('')
const turns = ref(0)
const result = ref(null)
const showSql = ref(false)
const copied = ref(false)
const chartEl = ref(null)
let chart = null
let copyTimer = null

const canAsk = computed(() => question.value.trim().length > 0 && !loading.value)

function useExample(text) {
  question.value = text
  ask()
}

async function ask() {
  if (!canAsk.value) return
  loading.value = true
  error.value = ''
  clarify.value = ''
  clarifyOptions.value = []
  result.value = null
  disposeChart()

  try {
    const resp = await fetch('/api/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question: question.value.trim(), session_id: sessionId.value || null })
    })
    const data = await resp.json()
    if (!resp.ok) throw new Error(data.detail || `请求失败（${resp.status}）`)
    sessionId.value = data.session_id || sessionId.value
    if (data.need_clarify) {
      clarify.value = data.clarify
      clarifyOptions.value = data.clarify_options || []
      return
    }
    result.value = data
    turns.value += 1
    await nextTick()
    renderChart()
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function newSession() {
  if (sessionId.value) {
    await fetch(`/api/session/${sessionId.value}`, { method: 'DELETE' }).catch(() => {})
  }
  sessionId.value = ''
  turns.value = 0
  result.value = null
  clarify.value = ''
  clarifyOptions.value = []
  error.value = ''
  disposeChart()
}

function disposeChart() {
  if (chart) {
    chart.dispose()
    chart = null
  }
}

function numericColumn() {
  const r = result.value
  if (!r) return null
  return r.columns.find((c) => r.rows.every((row) => typeof row[c] === 'number')) || null
}

function renderChart() {
  const r = result.value
  if (!r || !chartEl.value || r.rows.length === 0) return
  if (r.chart === 'metric' || r.chart === 'table') return

  const valueCol = numericColumn()
  if (!valueCol) return
  const labelCol = r.columns.find((c) => c !== valueCol)
  const labels = r.rows.map((row) => String(row[labelCol] ?? ''))
  const values = r.rows.map((row) => row[valueCol])

  chart = echarts.init(chartEl.value)
  const common = {
    grid: { left: 48, right: 24, top: 32, bottom: 56 },
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: labels, axisLabel: { rotate: labels.length > 7 ? 30 : 0 } },
    yAxis: { type: 'value' }
  }
  if (r.chart === 'pie') {
    chart.setOption({
      tooltip: { trigger: 'item' },
      series: [{
        type: 'pie', radius: '62%',
        data: r.rows.map((row) => ({ name: String(row[labelCol]), value: row[valueCol] }))
      }]
    })
  } else {
    chart.setOption({
      ...common,
      series: [{
        type: r.chart,
        data: values,
        smooth: r.chart === 'line',
        areaStyle: r.chart === 'line' ? { opacity: 0.12 } : undefined,
        itemStyle: { borderRadius: r.chart === 'bar' ? [4, 4, 0, 0] : 0 }
      }]
    })
  }
}

function fmt(v) {
  if (typeof v === 'number') return Number.isInteger(v) ? v : v.toFixed(2)
  return v ?? ''
}

async function copySql() {
  const sql = result.value?.sql
  if (!sql) return
  try {
    await navigator.clipboard.writeText(sql)
  } catch {
    // 非 https（比如直接 IP 访问）浏览器不给 clipboard 权限，退回 textarea 复制
    const ta = document.createElement('textarea')
    ta.value = sql
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    document.body.removeChild(ta)
  }
  copied.value = true
  clearTimeout(copyTimer)
  copyTimer = setTimeout(() => (copied.value = false), 1500)
}

function exportCsv() {
  const r = result.value
  if (!r || !r.rows.length) return
  // 逗号、引号、换行都要转义，否则 Excel 一打开就串列
  const cell = (v) => {
    const s = v === null || v === undefined ? '' : String(v)
    return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  const lines = [
    r.columns.join(','),
    ...r.rows.map((row) => r.columns.map((c) => cell(row[c])).join(','))
  ]
  // 前置 BOM：不加的话 Excel 双击打开中文全乱码
  const blob = new Blob(['﻿' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `askdata-${new Date().toISOString().slice(0, 19).replace(/[-:T]/g, '')}.csv`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

onBeforeUnmount(() => {
  disposeChart()
  clearTimeout(copyTimer)
})
</script>

<template>
  <div class="page">
    <header>
      <div class="top">
        <h1>AskData</h1>
        <button v-if="turns" class="chip" @click="newSession">新会话（{{ turns }} 轮）</button>
      </div>
      <p class="sub">用中文问数据 · 生成 SQL 在只读沙箱执行 · 结果自动出图</p>
    </header>

    <section class="ask">
      <textarea
        v-model="question"
        rows="2"
        placeholder="例如：上个月各门店的营收，按实收降序"
        @keydown.ctrl.enter="ask"
      />
      <button :disabled="!canAsk" @click="ask">
        {{ loading ? '生成中…' : '提问（Ctrl+Enter）' }}
      </button>
    </section>

    <div class="examples">
      <span>试试：</span>
      <button v-for="e in EXAMPLES" :key="e" class="chip" @click="useExample(e)">{{ e }}</button>
    </div>

    <p v-if="error" class="error">{{ error }}</p>
    <div v-if="clarify" class="clarify">
      <p>{{ clarify }}</p>
      <div v-if="clarifyOptions.length" class="examples">
        <button v-for="o in clarifyOptions" :key="o.question" class="chip" @click="useExample(o.question)">
          {{ o.label }}
        </button>
      </div>
    </div>

    <section v-if="result" class="result">
      <div class="head">
        <h2>{{ result.explanation || '查询结果' }}</h2>
        <div class="actions">
          <button class="link" @click="showSql = !showSql">
            {{ showSql ? '收起 SQL' : '查看 SQL' }}
          </button>
          <button class="link" @click="copySql">{{ copied ? '已复制' : '复制 SQL' }}</button>
          <button class="link" :disabled="!result.rows.length" @click="exportCsv">导出 CSV</button>
        </div>
      </div>

      <p v-if="result.warning" class="warn">结果可能有问题：{{ result.warning }}</p>

      <pre v-if="showSql" class="sql">{{ result.sql }}</pre>

      <div
        v-if="result.chart === 'metric'"
        class="metric"
      >
        {{ fmt(result.rows[0]?.[result.columns[0]]) }}
      </div>
      <div v-else-if="result.chart !== 'table'" ref="chartEl" class="chart" />

      <div class="table-wrap">
        <table>
          <thead>
            <tr><th v-for="c in result.columns" :key="c">{{ c }}</th></tr>
          </thead>
          <tbody>
            <tr v-for="(row, i) in result.rows" :key="i">
              <td v-for="c in result.columns" :key="c">{{ fmt(row[c]) }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <p class="meta">
        {{ result.row_count }} 行<template v-if="result.truncated">（已截断）</template>
        · SQL 执行 {{ result.query_ms }}ms
        · 共 {{ result.attempts }} 次生成
        · 总耗时 {{ result.elapsed_ms }}ms
        · 用到表：{{ result.tables_used.join('、') }}
      </p>

      <ul v-if="result.retry_log?.length" class="retry">
        <li v-for="(line, i) in result.retry_log" :key="i">{{ line }}</li>
      </ul>
    </section>
  </div>
</template>

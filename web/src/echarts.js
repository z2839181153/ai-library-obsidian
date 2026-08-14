// echarts 按需引入（P4 优化：全量包 ~1.1MB → 按需 ~400KB）
// 三个视图实际用到的：graph(占星室) / line(大厅趋势) / pie+bar(借书证画像)
import * as echarts from 'echarts/core'
import { GraphChart, LineChart, BarChart, PieChart } from 'echarts/charts'
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  MarkLineComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([
  GraphChart,
  LineChart,
  BarChart,
  PieChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  MarkLineComponent,
  CanvasRenderer,
])

export default echarts

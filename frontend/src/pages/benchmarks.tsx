import { FlaskConical } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { PageHeader } from '@/components/page-header'

export function BenchmarksPage() {
  return <div><PageHeader eyebrow="Наблюдение / позже" title="Benchmarks" description="Раздел зарезервирован для будущей функции сравнения запусков." /><Card><CardContent className="flex min-h-56 flex-col items-center justify-center gap-3 text-center"><FlaskConical className="size-7 text-copper" /><h2 className="text-base font-medium">Бенчмарки появятся позже</h2><p className="max-w-lg text-xs text-muted-foreground">В Version Zero здесь нет запуска тестов, измерения скорости или сравнения качества. Страница доступна как точка навигации.</p></CardContent></Card></div>
}

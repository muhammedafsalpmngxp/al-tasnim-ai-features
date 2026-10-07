<<<<<<< HEAD
import DailyMorningBrief from './pages/DailyMorningBrief/DailyMorningBrief'

export default function App() {
  return <DailyMorningBrief />
=======
import BootstrapGate from './components/Bootstrap/BootstrapGate'
import DailyMorningBrief from './pages/DailyMorningBrief/DailyMorningBrief'

export default function App() {
  return (
    <BootstrapGate>
      <DailyMorningBrief />
    </BootstrapGate>
  )
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
}

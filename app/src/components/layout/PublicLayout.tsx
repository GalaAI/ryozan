/**
 * PublicLayout: Wraps all public-facing site pages.
 *
 * Imports v2-redesign.css so all existing homepage/crew styles
 * apply without modification. The <Outlet /> renders the matched
 * child route (HomePage, CrewPage, etc).
 */

import { Outlet } from 'react-router-dom'
import '../../styles/v2-redesign.css'

export default function PublicLayout() {
  return (
    <div className="public-layout">
      <Outlet />
    </div>
  )
}

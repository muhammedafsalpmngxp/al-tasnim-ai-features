/**
 * The front page's way into the Oman Field Map. Deliberately light: it loads
 * no data and no map library, so the Daily Morning Brief costs nothing extra
 * for an operator who never opens the map.
 */
export default function FieldMapEntry({ onOpen }) {
  return (
    <section className="field-map-entry" aria-labelledby="field-map-entry-title">
      <div className="field-map-entry__text">
        <h2 id="field-map-entry-title" className="field-map-entry__title">Oman Field Map</h2>
        <p>
          Explore Oman&apos;s petroleum fields on satellite imagery, then filter a field&apos;s live wells
          by category, function, completion type and rig.
        </p>
      </div>
      <button type="button" className="btn btn--primary" onClick={onOpen}>
        Open Oman Field Map
      </button>
    </section>
  )
}

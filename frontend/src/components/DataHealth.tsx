export function DataHealth({ data }: { data: Record<string, string> }) {
  return (
    <div className="data-health">
      {Object.entries(data).map(([key, value]) => (
        <span key={key}>
          <strong>{key}</strong> {value}
        </span>
      ))}
    </div>
  );
}

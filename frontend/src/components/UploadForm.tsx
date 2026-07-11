import { useState } from "react";
import { ApiError, uploadDocument } from "../api/client";

interface UploadFormProps {
  /** Called once the backend has accepted the file and assigned a doc_id. */
  onUploaded: (docId: string, pdfName: string) => void;
}

/**
 * Step 1 of the workflow: pick a PDF and POST it to /upload.
 * Does not poll status itself — the parent switches to <StatusPoller /> once
 * `onUploaded` fires.
 */
export function UploadForm({ onUploaded }: UploadFormProps) {
  const [file, setFile] = useState<File | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!file) return;

    setIsUploading(true);
    setError(null);
    try {
      const response = await uploadDocument(file);
      onUploaded(response.doc_id, response.pdf_name);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Upload failed. Is the backend running?");
    } finally {
      setIsUploading(false);
    }
  }

  return (
    <form className="panel" onSubmit={handleSubmit}>
      <h2>Upload annual report</h2>
      <p className="muted">Select a PDF financial report (balance sheet / P&amp;L / cash flow / annual report).</p>
      <input
        type="file"
        accept="application/pdf"
        onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        disabled={isUploading}
      />
      <button type="submit" disabled={!file || isUploading}>
        {isUploading ? "Uploading…" : "Upload"}
      </button>
      {error && <p className="error">{error}</p>}
    </form>
  );
}

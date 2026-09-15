import React, { useRef, useState } from 'react';

interface PhotoCaptureProps {
  onCapture: (file: File, exifDateTime?: string) => void;
}

export const PhotoCapture: React.FC<PhotoCaptureProps> = ({ onCapture }) => {
  const [preview, setPreview] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const parseExifDate = (buffer: ArrayBuffer): string | undefined => {
    const dataView = new DataView(buffer);
    if (dataView.getUint16(0, false) !== 0xFFD8) return undefined;

    let offset = 2;
    while (offset < dataView.byteLength) {
      if (dataView.getUint16(offset, false) === 0xFFE1) {
        const length = dataView.getUint16(offset + 2, false);
        const exifMarker = String.fromCharCode(
          dataView.getUint8(offset + 4),
          dataView.getUint8(offset + 5),
          dataView.getUint8(offset + 6),
          dataView.getUint8(offset + 7)
        );

        if (exifMarker !== 'Exif') return undefined;

        const tiffOffset = offset + 10;
        const littleEndian = dataView.getUint16(tiffOffset, false) === 0x4949;
        
        const firstIfdOffset = dataView.getUint32(tiffOffset + 4, littleEndian);
        const entries = dataView.getUint16(tiffOffset + firstIfdOffset, littleEndian);

        for (let i = 0; i < entries; i++) {
          const entryOffset = tiffOffset + firstIfdOffset + 2 + (i * 12);
          const tag = dataView.getUint16(entryOffset, littleEndian);
          
          if (tag === 0x8769) { // ExifOffset
            const exifIfdOffset = dataView.getUint32(entryOffset + 8, littleEndian);
            const exifEntries = dataView.getUint16(tiffOffset + exifIfdOffset, littleEndian);
            
            for (let j = 0; j < exifEntries; j++) {
              const exifEntryOffset = tiffOffset + exifIfdOffset + 2 + (j * 12);
              const exifTag = dataView.getUint16(exifEntryOffset, littleEndian);
              
              if (exifTag === 0x9003) { // DateTimeOriginal
                const stringOffset = dataView.getUint32(exifEntryOffset + 8, littleEndian);
                let dateStr = '';
                for (let k = 0; k < 19; k++) {
                  dateStr += String.fromCharCode(dataView.getUint8(tiffOffset + stringOffset + k));
                }
                return dateStr;
              }
            }
          }
        }
        break;
      }
      offset += 2 + dataView.getUint16(offset + 2, false);
    }
    return undefined;
  };

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    // Generate preview
    const objectUrl = URL.createObjectURL(file);
    setPreview(objectUrl);

    // Read EXIF
    const arrayBuffer = await file.arrayBuffer();
    const exifDateTime = parseExifDate(arrayBuffer);

    onCapture(file, exifDateTime);
  };

  return (
    <div style={{ marginBottom: '16px' }}>
      <input
        type="file"
        accept="image/jpeg, image/jpg"
        capture="environment"
        ref={fileInputRef}
        onChange={handleFileChange}
        style={{ display: 'none' }}
      />
      
      {!preview ? (
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          style={{
            width: '100%',
            padding: '12px',
            backgroundColor: 'var(--tg-theme-button-color, #3390ec)',
            color: 'var(--tg-theme-button-text-color, #ffffff)',
            border: 'none',
            borderRadius: '8px',
            fontSize: '16px'
          }}
        >
          Сделать фото
        </button>
      ) : (
        <div>
          <img src={preview} alt="Preview" style={{ width: '100%', borderRadius: '8px' }} />
          <button
            type="button"
            onClick={() => {
              setPreview(null);
              if (fileInputRef.current) fileInputRef.current.value = '';
            }}
            style={{
              width: '100%',
              padding: '8px',
              marginTop: '8px',
              backgroundColor: 'transparent',
              color: 'var(--tg-theme-button-color, #3390ec)',
              border: '1px solid var(--tg-theme-button-color, #3390ec)',
              borderRadius: '8px',
              fontSize: '14px'
            }}
          >
            Переснять фото
          </button>
        </div>
      )}
    </div>
  );
};

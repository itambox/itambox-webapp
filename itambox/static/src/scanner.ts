class AssetScanner {
  private readerElement: HTMLElement | null;
  private modal: HTMLElement | null;
  private searchField: HTMLInputElement | null;
  private torchBtn: HTMLElement | null;

  private html5QrcodeScanner: Html5Qrcode | null = null;
  private isTorchOn: boolean = false;

  constructor() {
    this.readerElement = document.getElementById('scanner-reader') as HTMLElement | null;
    this.modal = document.getElementById('scanner-modal') as HTMLElement | null;
    this.searchField = document.getElementById('barcode-scan-input') as HTMLInputElement | null;
    this.torchBtn = document.getElementById('toggle-torch-btn') as HTMLElement | null;

    this.initEventListeners();
  }

  private initEventListeners(): void {
    const openBtn = document.getElementById('open-scanner-btn');
    const closeBtn = document.getElementById('close-scanner-btn');

    if (openBtn) {
      openBtn.addEventListener('click', () => this.start());
    }
    if (closeBtn) {
      closeBtn.addEventListener('click', () => this.stop());
    }
    if (this.torchBtn) {
      this.torchBtn.addEventListener('click', () => this.toggleTorch());
    }
  }

  public async start(): Promise<void> {
    if (!this.modal || !this.readerElement) {
      console.error('Scanner DOM elements missing');
      return;
    }

    this.modal.style.display = 'flex';

    try {
      // Initialize html5-qrcode using its hybrid mode (using BarcodeDetector natively if supported)
      this.html5QrcodeScanner = new Html5Qrcode('scanner-reader', {
        verbose: false,
        useBarCodeDetectorIfSupported: true
      });

      const config = {
        fps: 15,
        qrbox: (width: number, height: number) => {
          // Flexible viewfinder square box occupying 75% of shortest screen dimension
          const size = Math.min(width, height) * 0.75;
          return { width: Math.round(size), height: Math.round(size) };
        }
      };

      await this.html5QrcodeScanner.start(
        { facingMode: 'environment' },
        config,
        (decodedText: string) => {
          this.handleSuccess(decodedText);
        },
        (_errorMessage: string) => {
          // Silent catch: frame failures are normal before code is in view
        }
      );

      // Expose flashlight control if supported by hardware
      try {
        const capabilities = this.html5QrcodeScanner.getRunningTrackCapabilities();
        if (capabilities && (capabilities as any).torch && this.torchBtn) {
          this.torchBtn.style.display = 'block';
          this.isTorchOn = false;
        }
      } catch (capErr) {
        console.log('Flashlight capabilities not supported:', capErr);
      }

    } catch (err) {
      console.error('Camera/Scanner initialization failed:', err);
      this.stop();
    }
  }

  private handleSuccess(scannedValue: string): void {
    console.log(`Successful Scan: ${scannedValue}`);

    if (this.searchField) {
      this.searchField.value = scannedValue;
      this.searchField.dispatchEvent(new Event('input', { bubbles: true }));
      this.searchField.dispatchEvent(new Event('change', { bubbles: true }));
    }

    this.stop();
  }

  private async toggleTorch(): Promise<void> {
    if (this.html5QrcodeScanner && this.html5QrcodeScanner.isScanning) {
      try {
        this.isTorchOn = !this.isTorchOn;
        await this.html5QrcodeScanner.applyVideoConstraints({
          advanced: [{ torch: this.isTorchOn }]
        } as any);
      } catch (err) {
        console.warn('Torch toggle failed:', err);
      }
    }
  }

  public stop(): void {
    if (this.modal) {
      this.modal.style.display = 'none';
    }

    if (this.torchBtn) {
      this.torchBtn.style.display = 'none';
    }

    this.isTorchOn = false;

    if (this.html5QrcodeScanner) {
      if (this.html5QrcodeScanner.isScanning) {
        this.html5QrcodeScanner.stop().then(() => {
          if (this.html5QrcodeScanner) {
            this.html5QrcodeScanner.clear();
            this.html5QrcodeScanner = null;
          }
        }).catch(err => {
          console.error('Error stopping scanner:', err);
          this.html5QrcodeScanner = null;
        });
      } else {
        this.html5QrcodeScanner = null;
      }
    }
  }
}

// Function to initialize scanner when elements are present
function initAssetScanner(): void {
  const openBtn = document.getElementById('open-scanner-btn');
  if (openBtn) {
    window.AssetScannerInstance = new AssetScanner();
  }
}

// Hook into DOM lifecycles (both initial page load and HTMX swaps)
document.addEventListener('DOMContentLoaded', initAssetScanner);
document.body.addEventListener('htmx:afterSettle', initAssetScanner);

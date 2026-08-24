import PetSprite from './PetSprite';
import './VisualCopilotPet.css';

export default function VisualCopilotPet() {
  const startSelection = () => {
    window.electron?.ipcRenderer.sendMessage('selection-activate');
  };

  return (
    <main className="visual-copilot-pet" aria-label="Coco Visual Copilot">
      <div className="visual-copilot-pet__hint" aria-hidden>
        Select with Coco
      </div>
      <div className="visual-copilot-pet__sprite">
        <PetSprite mood="idle" variant="luma" />
        <span className="visual-copilot-pet__badge" aria-hidden>
          <span />
        </span>
        <button
          type="button"
          className="visual-copilot-pet__action"
          onClick={startSelection}
          title="Select an area with Coco"
          aria-label="Select an area with Coco"
        />
      </div>
    </main>
  );
}

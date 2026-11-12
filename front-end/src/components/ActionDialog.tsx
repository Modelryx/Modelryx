import { useEffect, useRef, type ReactNode } from 'react';

type ActionDialogProps = {
  title: string;
  onClose: () => void;
  children: ReactNode;
};

export function ActionDialog({ title, onClose, children }: ActionDialogProps) {
  const ref = useRef<HTMLDialogElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    returnFocus.current = document.activeElement as HTMLElement | null;
    dialog.showModal();
    return () => {
      dialog.close();
      returnFocus.current?.focus();
    };
  }, []);

  return (
    <dialog
      ref={ref}
      className="action-dialog"
      aria-label={title}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
    >
      <h2>{title}</h2>
      {children}
    </dialog>
  );
}

import { useState, useEffect, useRef } from "react";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Shield, ShieldOff, ArrowUpFromLine, ArrowDownToLine } from "lucide-react";

interface ClipboardSecuritySettings {
	out_enabled: boolean;
	out_max_bytes: number;
	rate_limit_bytes: number;
	rate_limit_window_seconds: number;
}

function formatBytes(bytes: number): string {
	if (bytes < 1024) return `${bytes} B`;
	if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
	return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function Clipboard() {
	const [dashboardClipboardContent, setDashboardClipboardContent] = useState('');
	const [clipboardImageUrl, setClipboardImageUrl] = useState<string | null>(null);
	const [securitySettings, setSecuritySettings] = useState<ClipboardSecuritySettings | null>(null);
	const fileInputRef = useRef<HTMLInputElement>(null);

	// --- Message Listener for Clipboard Updates and Server Settings ---
	useEffect(() => {
		const handleWindowMessage = (event: MessageEvent) => {
			if (event.origin !== window.location.origin) return;
			const message = event.data;

			if (typeof message === 'object' && message !== null) {
				if (message.type === 'clipboardContentUpdate') {
					if (typeof message.text === 'string') {
						setDashboardClipboardContent(message.text);
					}
				}
				// Listen for server settings with clipboard security config
				if (message.type === 'serverSettings' && message.payload?.clipboard_security) {
					setSecuritySettings(message.payload.clipboard_security);
				}
			}
		};

		window.addEventListener('message', handleWindowMessage);
		return () => window.removeEventListener('message', handleWindowMessage);
	}, []);

	const handleClipboardChange = (event: React.ChangeEvent<HTMLTextAreaElement>) => {
		setDashboardClipboardContent(event.target.value);
	};

	const handleClipboardBlur = (event: React.FocusEvent<HTMLTextAreaElement>) => {
		window.postMessage({ type: 'clipboardUpdateFromUI', text: event.target.value }, window.location.origin);
	};

	const handleImageUpload = (event: React.ChangeEvent<HTMLInputElement>) => {
		const file = event.target.files?.[0];
		if (file && file.type.startsWith('image/')) {
			const reader = new FileReader();
			reader.onload = (e) => {
				const result = e.target?.result as string;
				setClipboardImageUrl(result);

				// Convert to blob and send to clipboard
				fetch(result)
					.then(res => res.blob())
					.then(blob => {
						window.postMessage({
							type: 'clipboardImageUpdate',
							imageBlob: blob
						}, window.location.origin);
					})
					.catch(err => console.error('Error processing image:', err));
			};
			reader.readAsDataURL(file);
		}
	};

	const handleImageButtonClick = () => {
		fileInputRef.current?.click();
	};

	const handleClearImage = () => {
		setClipboardImageUrl(null);
		if (fileInputRef.current) {
			fileInputRef.current.value = '';
		}
	};

	return (
		<div className="w-[300px] p-4 flex flex-col gap-3">
			{/* Security Settings Display */}
			{securitySettings && (
				<div className="rounded-lg border bg-muted/50 p-3 space-y-2">
					<div className="flex items-center gap-2 text-sm font-medium">
						{securitySettings.out_enabled ? (
							<Shield className="h-4 w-4 text-green-500" />
						) : (
							<ShieldOff className="h-4 w-4 text-red-500" />
						)}
						<span>Clipboard Security</span>
					</div>
					<div className="grid grid-cols-2 gap-2 text-xs text-muted-foreground">
						<div className="flex items-center gap-1">
							<ArrowDownToLine className="h-3 w-3" />
							<span>Paste IN: Always</span>
						</div>
						<div className="flex items-center gap-1">
							<ArrowUpFromLine className="h-3 w-3" />
							<span>Copy OUT: {securitySettings.out_enabled ? 'Limited' : 'Blocked'}</span>
						</div>
					</div>
					{securitySettings.out_enabled && (
						<div className="text-xs text-muted-foreground border-t pt-2 mt-2 space-y-1">
							<div>Max: {formatBytes(securitySettings.out_max_bytes)}/operation</div>
							<div>Rate: {formatBytes(securitySettings.rate_limit_bytes)}/{securitySettings.rate_limit_window_seconds}s</div>
						</div>
					)}
				</div>
			)}

			<Label htmlFor="dashboardClipboardTextarea">Clipboard</Label>
			<Textarea
				id="dashboardClipboardTextarea"
				value={dashboardClipboardContent}
				onChange={handleClipboardChange}
				onBlur={handleClipboardBlur}
				rows={5}
				placeholder="Enter text to copy to remote clipboard..."
				className="allow-native-input resize-none bg-background/95 overflow-y-auto max-h-[150px]"
			/>

			{/* Image Upload Section */}
			<div className="flex flex-col gap-2">
				<div className="flex gap-2">
					<Button
						variant="outline"
						size="sm"
						onClick={handleImageButtonClick}
						className="flex-1"
					>
						Upload Image
					</Button>
					{clipboardImageUrl && (
						<Button
							variant="outline"
							size="sm"
							onClick={handleClearImage}
							className="flex-1"
						>
							Clear Image
						</Button>
					)}
				</div>

				<input
					ref={fileInputRef}
					type="file"
					accept="image/*"
					onChange={handleImageUpload}
					className="hidden"
				/>

				{clipboardImageUrl && (
					<div className="mt-2">
						<img
							src={clipboardImageUrl}
							alt="Clipboard preview"
							className="max-w-full max-h-32 object-contain rounded border"
						/>
					</div>
				)}
			</div>
		</div>
	);
}

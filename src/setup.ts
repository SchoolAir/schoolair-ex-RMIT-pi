/**
* Pi setup and init
* Manages the AUTH_TOKEN. If its valid, startup continues as normal.
* If not, the user is prompted to register the device.
*/

import "dotenv/config";
import fs from "fs";
import readlineSync from "readline-sync";
import os from "os";

const prompt = (q: string, options = {}) => readlineSync.question(q, options);
const secret = (q: string) => readlineSync.question(q, { hideEchoBack: true });
const envContent = fs.readFileSync(".env", "utf-8");

const printBanner = () => {
  console.log(`
┌─────────────────────────────┐
│          SchoolAir          │
│     Device Registration     │
└─────────────────────────────┘
`);
};

const getMacAddress = () => {
	const interfaces = os.networkInterfaces();
	for (const name of Object.keys(interfaces)) {
		const iface = interfaces[name];
		if (!iface) continue;
		for (const net of iface) {
			if (!net.internal && net.mac && net.mac !== "00:00:00:00:00:00") {
				return net.mac;
			}
		}
	}
	throw new Error("Unable to determine MAC address");
};

export const ensureRegistered = async () => {
	const token = process.env.AUTH_TOKEN?.trim();

	if (token) {
		try {
			const res = await fetch(`${process.env.SERVER_URL}/aqc/v1/validate`, {
				headers: { Authorization: `Bearer ${token}` },
			});
			if (res.ok) return; // all good, continue startup
			console.log("Token invalid or expired, please re-register.");
		} catch {
			console.error("Could not reach server to validate token, check your connection.");
			process.exit(1);
		}
	} else {
		console.log("Auth token missing/empty, please register.");
	}

	printBanner();

	// Details for registration
	const mac_address = getMacAddress();
	const org_token = prompt("Organisation Token: ");
	const teacher_user = prompt("Teacher Username: ");
	const teacher_pass = secret("Teacher Password: ");
	const device_name = prompt("Device Nickname: ");
	const location = prompt("Device Location (indoor/outdoor): ", {
		limit: ["indoor", "outdoor"],
		caseSensitive: false,
	}).toLowerCase();

	// Try and register with server
	try {
		const res = await fetch(`${process.env.SERVER_URL}/aqc/v1/register`, {
			method: "POST",
			headers: { 
				Authorization: `Bearer ${org_token}`, 
				"Content-Type": "application/json"
			},
			
			body: JSON.stringify({
				mac_address,
				nickname: device_name,
				device_type: location,
				username: teacher_user,
				password: teacher_pass,
			}),
		});

		if (!res.ok) {
			const error = await res.json() as { error: string };
			throw new Error(error.error || "Registration failed");
		}

		const data = await res.json() as { message: string; auth_token: string };
		console.log(data.message + "\n");
		const updated = envContent.replace(/^AUTH_TOKEN=.*$/m, `AUTH_TOKEN=${data.auth_token}`);
		fs.writeFileSync(".env", updated);
	} catch (err) {
		console.error("Registration failed:", err instanceof Error ? err.message : err);
		process.exit(1);
	}
};
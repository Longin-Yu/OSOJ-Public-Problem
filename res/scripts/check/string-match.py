from sys import argv
try: 
  if argv[1].strip() == argv[2].strip(): exit(0)
except: exit(1)
exit(1)